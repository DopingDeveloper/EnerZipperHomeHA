"""Read EnerZipper measurements from SmartThings' Android cache, without APIs."""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

APP_DB = '/data/user/0/com.samsung.android.oneconnect/databases/DataLayerData.db'
DEFAULT_ADB = 'adb'


class ReadError(Exception):
    """A public, non-sensitive failure reason."""


def iso(timestamp):
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def metric(row, expected_unit, now, max_age):
    if row is None:
        raise ReadError('measurement_missing')
    if row['unit'] != expected_unit:
        raise ReadError('unexpected_unit')
    try:
        value = json.loads(row['stringifyValue'])
        timestamp = int(row['timestamp']) / 1000
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError()
        if not math.isfinite(value) or value < 0 or timestamp <= 0 or timestamp > now + 120:
            raise ValueError()
        updated = iso(timestamp)
    except (ValueError, TypeError, OverflowError, OSError):
        raise ReadError('invalid_measurement') from None
    age = max(0, now - timestamp)
    return dict(value=value, unit=expected_unit, source_updated_at=updated,
                age_seconds=round(age, 3), stale=age > max_age)


def extract(db, now, max_age, device_label='EnerZipperHome'):
    db.row_factory = sqlite3.Row
    devices = db.execute("SELECT deviceId FROM DeviceDomain WHERE name=? OR label=?",
                         (device_label, device_label)).fetchall()
    if len(devices) != 1:
        raise ReadError('target_missing_or_ambiguous')
    output = {}
    for name, capability, attribute, unit in [('power', 'powerMeter', 'power', 'W'),
                                               ('energy', 'energyMeter', 'energy', 'kWh')]:
        rows = db.execute('SELECT stringifyValue,unit,timestamp FROM DeviceCapabilityStatusDomain '
                          'WHERE deviceId=? AND componentId=? AND capabilityId=? AND attributeName=?',
                          (devices[0]['deviceId'], 'main', capability, attribute)).fetchall()
        if len(rows) != 1:
            raise ReadError('measurement_missing_or_ambiguous')
        output[name] = metric(rows[0], unit, now, max_age)
    output.update(captured_at=iso(now), reader_status='stale' if any(
        item['stale'] for item in output.values()) else 'ok')
    return output


def adb_cat(adb, serial, suffix=''):
    result = subprocess.run([adb, '-s', serial, 'exec-out', 'cat', APP_DB + suffix],
                            capture_output=True, timeout=15)
    if result.returncode:
        if suffix and b'No such file' in result.stderr:
            return None
        raise ReadError('adb_read_failed')
    return result.stdout


def read_once(args):
    # Compare both reads to reject a checkpoint or write during the snapshot.
    for attempt in range(3):
        base, wal = adb_cat(args.adb, args.serial), adb_cat(args.adb, args.serial, '-wal')
        if base != adb_cat(args.adb, args.serial) or wal != adb_cat(args.adb, args.serial, '-wal'):
            continue
        with tempfile.TemporaryDirectory(prefix='enerzipper-cache-') as directory:
            os.chmod(directory, 0o700)
            path = Path(directory) / 'cache.db'
            path.write_bytes(base)
            if wal:
                path.with_name('cache.db-wal').write_bytes(wal)
            db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)
            try:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    continue
                return extract(db, time.time(), args.max_age, getattr(args, 'device_label', 'EnerZipperHome'))
            finally:
                db.close()
    raise ReadError('cache_snapshot_unstable')


def save_atomic(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.enerzipper-',
                                     encoding='utf-8', delete=False) as output:
        temporary = Path(output.name)
        try:
            os.chmod(temporary, 0o600)
            output.write(data + '\n')
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def self_test():
    import unittest
    class ValidationTests(unittest.TestCase):
        def row(self, value='123.4', unit='W', timestamp='1000000'):
            return dict(stringifyValue=value, unit=unit, timestamp=timestamp)
        def test_fresh_and_stale(self):
            self.assertFalse(metric(self.row(), 'W', 1010, 180)['stale'])
            self.assertTrue(metric(self.row(), 'W', 1200, 180)['stale'])
        def test_invalid_is_never_zero(self):
            for value in ['null', 'true', '-1', 'NaN', '"12"']:
                with self.assertRaises(ReadError): metric(self.row(value=value), 'W', 1010, 180)
            with self.assertRaises(ReadError): metric(self.row(unit='kW'), 'W', 1010, 180)
            with self.assertRaises(ReadError): metric(self.row(timestamp='2000000'), 'W', 1010, 180)
        def test_sqlite_target_filter(self):
            db = sqlite3.connect(':memory:')
            db.execute('CREATE TABLE DeviceDomain(deviceId,name,label)')
            db.execute('CREATE TABLE DeviceCapabilityStatusDomain(deviceId,componentId,capabilityId,attributeName,stringifyValue,unit,timestamp)')
            db.executemany('INSERT INTO DeviceDomain VALUES(?,?,?)', [('target','EnerZipper Home','EnerZipperHome'),('other','Other','Other')])
            db.executemany('INSERT INTO DeviceCapabilityStatusDomain VALUES(?,?,?,?,?,?,?)', [('target','main','powerMeter','power','123.4','W','1000000'),('target','main','energyMeter','energy','456.7','kWh','1000000'),('other','main','powerMeter','power','999','W','1000000')])
            self.assertEqual(extract(db, 1010, 180)['power']['value'], 123.4)
            db.execute("INSERT INTO DeviceDomain VALUES('duplicate','Duplicate','EnerZipperHome')")
            with self.assertRaises(ReadError): extract(db, 1010, 180)
            db.close()
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(ValidationTests))
    return 0 if result.wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', default=DEFAULT_ADB)
    parser.add_argument('--serial', default='emulator-5556')
    parser.add_argument('--device-label', default='EnerZipperHome', help='Exact SmartThings device label or name')
    parser.add_argument('--max-age', type=int, default=180)
    parser.add_argument('--samples', type=int, default=1)
    parser.add_argument('--interval', type=float, default=30)
    parser.add_argument('--output')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not 1 <= args.samples <= 10 or not 5 <= args.interval <= 60 or not 1 <= args.max_age <= 3600:
        parser.error('samples must be 1..10, interval 5..60, max-age 1..3600')
    failed = False
    for sample in range(args.samples):
        try:
            result = read_once(args)
        except (ReadError, OSError, subprocess.SubprocessError, sqlite3.Error) as error:
            failed = True
            result = dict(captured_at=iso(time.time()), reader_status='error',
                          error=str(error) if isinstance(error, ReadError) else type(error).__name__,
                          power=None, energy=None)
        data = json.dumps(result, allow_nan=False)
        if args.output:
            save_atomic(args.output, data)
        print(data, flush=True)
        if sample + 1 < args.samples:
            time.sleep(args.interval)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
