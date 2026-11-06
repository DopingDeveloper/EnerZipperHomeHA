# 읽기 경로와 검증 범위

## 읽는 데이터

Android 앱의 아래 데이터베이스를 `adb exec-out cat`으로 임시 디렉터리에 복사합니다.

```text
/data/user/0/com.samsung.android.oneconnect/databases/DataLayerData.db
/data/user/0/com.samsung.android.oneconnect/databases/DataLayerData.db-wal
```

원본 DB에는 쓰지 않습니다. 원본의 base/WAL을 두 번 읽어 동일한지 확인하고, 복사본은 SQLite `mode=ro`로 열어 `PRAGMA quick_check`를 실행합니다. 원본이 계속 변하면 최대 세 번 시도 후 오류로 처리합니다. 임시 디렉터리는 `0700`, 종료 후 자동 삭제됩니다. 이 방법이 앱의 정식 snapshot API와 동일한 트랜잭션 보장을 제공하는 것은 아닙니다. 검사가 실패한 데이터는 게시하지 않습니다.

| 테이블 | 사용하는 열 |
|---|---|
| `DeviceDomain` | `deviceId`, `name`, `label` |
| `DeviceCapabilityStatusDomain` | `deviceId`, `componentId`, `capabilityId`, `attributeName`, `stringifyValue`, `unit`, `timestamp` |

`device_label`과 name 또는 label이 정확히 일치하는 기기가 **하나**여야 합니다. `deviceId`는 메모리 안에서 행을 연결하는 데만 사용하며 출력하지 않습니다. main 컴포넌트의 아래 두 값만 반환합니다.

| capability / attribute | 단위 | HA 의미 |
|---|---|---|
| `powerMeter` / `power` | W | 현재 전력, measurement |
| `energyMeter` / `energy` | kWh | 누적 전력량, total_increasing |

다른 기기 측정값, 계정 토큰, OCF 키는 반환하지 않습니다. 단, SQLite 파일 단위로 복사하므로 개인 임시 디렉터리에는 잠깐 **앱 전체 DB 사본**이 생깁니다. 처리 후 삭제되며 공개 저장소에 업로드할 파일이 아닙니다.

## 신선도와 오류

- 원본 `timestamp`는 Unix milliseconds이며 출력은 UTC ISO 8601입니다.
- `captured_at`은 읽은 시각, `source_updated_at`은 앱이 기록한 측정 시각입니다.
- 현재 시각보다 120초 넘게 미래인 시각, 숫자가 아닌 값, NaN/무한대/음수, W/kWh 단위 불일치, 누락·중복 대상은 거부합니다.
- 기본 허용 나이는 180초입니다. 한 값이라도 오래되면 전체 장치를 offline으로 처리합니다.
- 오류/오래된 값을 0으로 치환하지 않습니다. 누적량의 가짜 리셋이나 허위 0W 기록을 방지합니다.
- 정상 값에서 0W는 유효합니다. 오류를 0으로 만들지 않는 것과 구분합니다.

## MQTT 계약

기본 토픽은 `enerzipper/home_meter`이며 설정으로 바꿀 수 있습니다.

| 토픽 | QoS | Retain | 내용 |
|---|---:|---|---|
| `homeassistant/sensor/enerzipper_cache_home_meter_power/config` | 1 | true | 전력 센서 자동 검색 |
| `homeassistant/sensor/enerzipper_cache_home_meter_energy/config` | 1 | true | 누적량 센서 자동 검색 |
| `enerzipper/home_meter/power/state` | 1 | false | 숫자 W |
| `enerzipper/home_meter/energy/state` | 1 | false | 숫자 kWh |
| `enerzipper/home_meter/{power,energy}/attributes` | 1 | false | 원본 측정 시각·출처 |
| `enerzipper/home_meter/availability` | 1 | true | online / offline |

시작·재연결·HA의 MQTT birth(기본 `homeassistant/status = online`) 때 discovery와 신선한 상태를 재게시합니다. 원본 시각이 같은 상태는 중복 게시하지 않습니다. 상태는 retain하지 않으므로 HA 재시작 후 오래된 수치를 재생하지 않습니다. 마지막 값이 기록에 남아 있어도 availability가 offline이면 현재 사용 가능한 값이 아닙니다.

브리지 종료는 offline을 게시합니다. 비정상 연결 종료는 MQTT Last Will로 offline이 됩니다. 원본 읽기 장애에서도 offline이 됩니다. MQTT 센서의 `expire_after`는 `max_age + 3 × poll_interval`입니다. 브리지 프로세스가 멈춘 채 네트워크 스레드만 살아남는 경우에도 값이 영구적으로 현재 값처럼 남지 않도록 합니다. HA birth topic을 사용자 정의했다면 현재 코드의 기본 `<discovery_prefix>/status` 구독과 맞는지 확인하세요.

브로커 인증값은 환경 변수에서 읽습니다. MQTT TLS를 켜면 Paho 기본 서버 인증서 검증을 사용하며 검증 생략 옵션은 없습니다. MQTT 브리지는 계측값 송신 전용으로, 기기 제어 명령 토픽을 구독하지 않습니다.

## 검증 범위

### 실제 기기·앱에서 검증

2026-11-06, 공식 SmartThings 1.8.52.23, root 가능한 Android 17 Google APIs ARM64 에뮬레이터, Apple Silicon Mac mini의 조합입니다.

- 전력·누적량·원본 시각을 읽고, 여러 시각의 갱신 확인.
- 동일 시각의 기존 HA SmartThings 센서 기록과 수치 일치.
- 독립 실행 추출기를 Mac Python 3.9.6에서 실행하고 연속 세 샘플 확인.
- PSK/Frida/OCR/기기 재등록 없이 실행.

실제 주소, 계정 ID, 기기 UUID, DB 사본, APK, 패킷과 원본 측정 로그는 이 저장소에 넣지 않습니다. 예시는 합성 데이터입니다.

### 자동 테스트

- 합성 SQLite에서 대상 기기 필터·중복 대상 거부.
- 신선도, 잘못된 단위, 비정상 값, 미래 시각 거부.
- discovery 단위·state_class, 중복 측정 억제, stale/error 시 offline.
- 실제 Paho client가 loopback 테스트 MQTT 서버에 QoS 1로 discovery·측정·offline을 보내고 ACK를 처리하는지 확인.

테스트 MQTT 서버는 제품용 브로커가 아닙니다. 일반 브로커 전체 동작, TLS, 브로커 재시작, 실제 HA discovery 처리, 사용자 계정 변경은 별도 운용 검증 대상입니다. 클라우드 연결을 끊고도 측정된다는 테스트는 하지 않았으며 그런 기능을 제공하지 않습니다.
