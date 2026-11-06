"""SmartThings Android cache -> MQTT discovery. No SmartThings API credentials."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import threading
import time
from types import SimpleNamespace

from read_enerzipper_cache import read_once


def load_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    defaults = dict(adb='adb', serial='emulator-5556', device_label='EnerZipperHome',
                    poll_interval=30, max_age=180, instance_id='home_meter',
                    topic_prefix='enerzipper', discovery_prefix='homeassistant')
    defaults.update(config)
    config = defaults
    for key in ('instance_id', 'topic_prefix', 'discovery_prefix'):
        pattern = r'[A-Za-z0-9_-]+' if key == 'instance_id' else r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*'
        if not isinstance(config[key], str) or not re.fullmatch(pattern, config[key]):
            raise ValueError('invalid_' + key)
    for key, lower, upper in [('poll_interval', 5, 300), ('max_age', 30, 3600)]:
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lower <= value <= upper:
            raise ValueError('invalid_' + key)
    for key in ('adb', 'serial', 'device_label'):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError('invalid_' + key)
    broker = config.get('mqtt', {})
    if not isinstance(broker, dict) or not isinstance(broker.get('host'), str) or not broker['host'].strip():
        raise ValueError('mqtt_host_required')
    port = broker.setdefault('port', 1883)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('invalid_mqtt_port')
    if not isinstance(broker.get('tls', False), bool):
        raise ValueError('invalid_mqtt_tls')
    return config


def topics(config):
    return config['topic_prefix'] + '/' + config['instance_id']


def discovery_messages(config):
    base = topics(config)
    device = {'identifiers': ['enerzipper_cache_' + config['instance_id']],
              'name': 'EnerZipper Home (Android cache)', 'manufacturer': 'Entec System',
              'model': 'SmartThings app cache bridge'}
    result = []
    for name, unit, state_class in [('power', 'W', 'measurement'), ('energy', 'kWh', 'total_increasing')]:
        identifier = 'enerzipper_cache_' + config['instance_id'] + '_' + name
        payload = dict(name='Power' if name == 'power' else 'Energy', unique_id=identifier,
                       state_topic=base + '/' + name + '/state',
                       json_attributes_topic=base + '/' + name + '/attributes',
                       availability_topic=base + '/availability',
                       unit_of_measurement=unit, device_class=name, state_class=state_class,
                       expire_after=int(config['max_age'] + 3 * config['poll_interval']),
                       device=device, origin={'name': 'EnerZipperHomeHA', 'sw_version': '0.1.0'})
        result.append((config['discovery_prefix'] + '/sensor/' + identifier + '/config', payload))
    return result


class Publisher:
    def __init__(self, client, config):
        self.client, self.config = client, config
        self.base = topics(config)
        self.sent = {}

    def send(self, topic, payload, retain=False):
        if isinstance(payload, dict):
            payload = json.dumps(payload, allow_nan=False)
        info = self.client.publish(topic, payload, qos=1, retain=retain)
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            raise TimeoutError('mqtt_publish_timeout')

    def offline(self):
        self.send(self.base + '/availability', 'offline', retain=True)

    def discover(self):
        self.offline()
        for topic, payload in discovery_messages(self.config):
            self.send(topic, payload, retain=True)
        self.sent.clear()

    def process(self, sample):
        if sample.get('reader_status') != 'ok' or any(
                not sample.get(key) or sample[key].get('stale', True) for key in ('power', 'energy')):
            self.offline()
            self.sent.clear()
            return False
        for key in ('power', 'energy'):
            value = sample[key]
            if self.sent.get(key) != value['source_updated_at']:
                self.send(self.base + '/' + key + '/attributes',
                          {'source_updated_at': value['source_updated_at'], 'source': 'SmartThings Android cache'})
                # Do not retain measurements: a restart must not resurrect an old measurement.
                self.send(self.base + '/' + key + '/state', str(value['value']))
                self.sent[key] = value['source_updated_at']
        self.send(self.base + '/availability', 'online', retain=True)
        return True


def run(config, once=False):
    import paho.mqtt.client as mqtt
    stop, connected, rediscover, wake = (threading.Event() for _ in range(4))
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id='enerzipper_cache_' + config['instance_id'])
    broker = config['mqtt']
    username = os.environ.get(broker.get('username_env', 'ENERZIPPER_MQTT_USERNAME'))
    password_key = broker.get('password_env', 'ENERZIPPER_MQTT_PASSWORD')
    password = os.environ.get(password_key)
    if username and not password:
        raise ValueError('mqtt_password_environment_missing')
    if password and not username:
        raise ValueError('mqtt_username_environment_missing')
    if username:
        client.username_pw_set(username, password)
    if broker.get('tls', False):
        client.tls_set(ca_certs=broker.get('ca_file'))
    client.will_set(topics(config) + '/availability', 'offline', qos=1, retain=True)
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe(config['discovery_prefix'] + '/status', qos=1)
            rediscover.set()
            connected.set()
            wake.set()

    def on_disconnect(client, userdata, flags, reason_code, properties):
        connected.clear()

    def on_message(client, userdata, message):
        if message.payload == b'online':
            rediscover.set()
            wake.set()

    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
    previous_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    for signum in previous_handlers:
        signal.signal(signum, lambda *_: (stop.set(), wake.set()))
    publisher = Publisher(client, config)
    client.connect_async(broker['host'], broker['port'], keepalive=30)
    client.loop_start()
    deadline = time.monotonic() + 30
    exit_code = 0
    try:
        while not stop.is_set():
            if not connected.wait(timeout=1):
                if once and time.monotonic() > deadline:
                    raise TimeoutError('mqtt_connection_timeout')
                continue
            wake.clear()
            try:
                if rediscover.is_set():
                    rediscover.clear()
                    publisher.discover()
                sample = read_once(SimpleNamespace(**config))
                good = publisher.process(sample)
                print(json.dumps({'event': 'sample', 'status': sample['reader_status']}), flush=True)
                if not good:
                    exit_code = 1
            except Exception as error:
                # Print the exception class only: library errors may contain paths or credentials.
                print(json.dumps({'event': 'sample_error', 'kind': type(error).__name__}), flush=True)
                try:
                    publisher.offline()
                except Exception:
                    pass
                exit_code = 1
            if once:
                break
            wake.wait(config['poll_interval'])
    finally:
        if connected.is_set():
            try:
                publisher.offline()
            except Exception:
                pass
        client.disconnect()
        client.loop_stop()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--once', action='store_true', help='Publish one sample then disconnect (availability becomes offline)')
    parser.add_argument('--dry-run', action='store_true', help='Read one sample without connecting to MQTT')
    parser.add_argument('--print-discovery', action='store_true', help='Print discovery payloads without connecting')
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.print_discovery:
            print(json.dumps(discovery_messages(config), indent=2))
            return 0
        if args.dry_run:
            sample = read_once(SimpleNamespace(**config))
            print(json.dumps(sample, allow_nan=False))
            return 0 if sample['reader_status'] == 'ok' else 1
        return run(config, once=args.once)
    except Exception as error:
        print(json.dumps({'event': 'fatal', 'kind': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
