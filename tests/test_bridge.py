import copy
import importlib
import json
import socket
import struct
import threading
import unittest
from unittest.mock import patch

from enerzipper_bridge import Publisher, discovery_messages, load_config, run
from read_enerzipper_cache import self_test

CONFIG = dict(instance_id='test_meter', topic_prefix='enerzipper',
              discovery_prefix='homeassistant', poll_interval=30, max_age=180)


def sample():
    return dict(reader_status='ok', power=dict(value=123.4, unit='W',
                source_updated_at='2026-01-01T00:00:00Z', stale=False),
                energy=dict(value=1234.567, unit='kWh',
                source_updated_at='2026-01-01T00:00:00Z', stale=False))


class Published:
    def wait_for_publish(self, timeout):
        pass

    def is_published(self):
        return True


class RecordingClient:
    def __init__(self):
        self.messages = []

    def publish(self, topic, payload, qos, retain):
        self.messages.append((topic, payload, qos, retain))
        return Published()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.client = RecordingClient()
        self.publisher = Publisher(self.client, CONFIG)

    def test_discovery_and_state_contract(self):
        self.publisher.discover()
        configs = [json.loads(payload) for topic, payload, _, retained in self.client.messages
                   if topic.endswith('/config') and retained]
        self.assertEqual(len(configs), 2)
        self.assertEqual({p['state_class'] for p in configs}, {'measurement', 'total_increasing'})
        self.publisher.process(sample())
        states = [(topic, payload, retained) for topic, payload, _, retained in self.client.messages
                  if topic.endswith('/state')]
        self.assertEqual([p for _, p, _ in states], ['123.4', '1234.567'])
        self.assertTrue(all(not retained for _, _, retained in states))
        self.assertEqual(self.client.messages[-1][1], 'online')

    def test_duplicate_does_not_republish_measurements(self):
        self.publisher.process(sample())
        before = len([m for m in self.client.messages if m[0].endswith('/state')])
        self.publisher.process(sample())
        self.assertEqual(before, len([m for m in self.client.messages if m[0].endswith('/state')]))
        self.publisher.discover()  # reconnect or Home Assistant birth
        self.publisher.process(sample())
        self.assertEqual(before + 2, len([m for m in self.client.messages if m[0].endswith('/state')]))

    def test_stale_or_error_publishes_offline_without_zero(self):
        for bad in ({'reader_status': 'error'}, copy.deepcopy(sample())):
            if 'energy' in bad:
                bad['energy']['stale'] = True
            self.client.messages.clear()
            self.assertFalse(self.publisher.process(bad))
            self.assertEqual(len(self.client.messages), 1)
            self.assertEqual(self.client.messages[0][1], 'offline')

    def test_failed_publish_is_not_marked_sent(self):
        self.client.publish = lambda *args, **kwargs: type('Unsent', (), {
            'wait_for_publish': lambda self, timeout: None,
            'is_published': lambda self: False})()
        with self.assertRaises(TimeoutError):
            self.publisher.process(sample())
        self.assertEqual(self.publisher.sent, {})

    def test_sample_config_has_no_secrets(self):
        config = load_config('config.example.json')
        self.assertNotIn('password', config['mqtt'])
        self.assertNotIn('username', config['mqtt'])
        self.assertEqual(config['mqtt']['password_env'], 'ENERZIPPER_MQTT_PASSWORD')

    def test_reader_boundaries(self):
        self.assertEqual(self_test(), 0)


class LocalBroker:
    """Tiny loopback MQTT 3.1.1 test peer: CONNECT/PUBLISH/PING/DISCONNECT only."""
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.listener.settimeout(5)
        self.port = self.listener.getsockname()[1]
        self.messages, self.errors = [], []
        self.thread = threading.Thread(target=self.serve, daemon=True)

    @staticmethod
    def exact(connection, length):
        result = b''
        while len(result) < length:
            chunk = connection.recv(length - len(result))
            if not chunk:
                raise EOFError()
            result += chunk
        return result

    def serve(self):
        try:
            with self.listener.accept()[0] as connection:
                connection.settimeout(5)
                while True:
                    header = self.exact(connection, 1)[0]
                    length, factor = 0, 1
                    while True:
                        digit = self.exact(connection, 1)[0]
                        length += (digit & 127) * factor
                        factor *= 128
                        if not digit & 128:
                            break
                    body = self.exact(connection, length)
                    kind = header >> 4
                    if kind == 1:
                        connection.sendall(b'\x20\x02\x00\x00')
                    elif kind == 3:
                        size = struct.unpack('!H', body[:2])[0]
                        topic = body[2:2 + size].decode()
                        offset = size + 2
                        if ((header >> 1) & 3) == 1:
                            packet_id = body[offset:offset + 2]
                            offset += 2
                            connection.sendall(b'\x40\x02' + packet_id)
                        self.messages.append((topic, body[offset:].decode(), bool(header & 1)))
                    elif kind == 12:
                        connection.sendall(b'\xd0\x00')
                    elif kind == 8:
                        connection.sendall(b'\x90\x03' + body[:2] + b'\x01')
                    elif kind == 14:
                        break
                    else:
                        raise AssertionError('Unexpected packet type')
        except Exception as error:
            self.errors.append(type(error).__name__)
        finally:
            self.listener.close()


class MqttWireTests(unittest.TestCase):
    def test_complete_once_run_reads_and_disconnects(self):
        broker = LocalBroker()
        broker.thread.start()
        config = dict(CONFIG, mqtt=dict(host='127.0.0.1', port=broker.port,
                      username_env='ENERZIPPER_TEST_UNSET_USER', password_env='ENERZIPPER_TEST_UNSET_PASSWORD'))
        with patch('enerzipper_bridge.read_once', return_value=sample()) as reader:
            self.assertEqual(run(config, once=True), 0)
        broker.thread.join(5)
        reader.assert_called_once()
        self.assertEqual(broker.errors, [])
        self.assertEqual(broker.messages[-1][1], 'offline')

    def test_actual_paho_qos1_publish(self):
        mqtt = importlib.import_module('paho.mqtt.client')
        broker = LocalBroker()
        broker.thread.start()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='enerzipper_test')
        ready = threading.Event()
        client.on_connect = lambda *args: ready.set()
        try:
            client.connect('127.0.0.1', broker.port, 30)
            client.loop_start()
            self.assertTrue(ready.wait(5))
            publisher = Publisher(client, CONFIG)
            publisher.discover()
            self.assertTrue(publisher.process(sample()))
            publisher.offline()
        finally:
            client.disconnect()
            client.loop_stop()
            broker.thread.join(5)
        self.assertFalse(broker.thread.is_alive())
        self.assertEqual(broker.errors, [])
        self.assertEqual(len([m for m in broker.messages if m[0].endswith('/config')]), 2)
        self.assertEqual(broker.messages[-1][1], 'offline')
        self.assertFalse(any(retain for topic, _, retain in broker.messages if topic.endswith('/state')))


if __name__ == '__main__':
    unittest.main()
