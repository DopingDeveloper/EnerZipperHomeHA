# EnerZipperHomeHA

SmartThings **Android 앱이 이미 수신한 EnerZipper Home 측정값**을 읽어 Home Assistant로 전달하는 실험용 브리지입니다.

```text
EnerZipper Home → 기존 클라우드 → 공식 SmartThings Android 앱
                                      ↓ 앱 SQLite 캐시 (읽기 전용)
                                  Python 추출기 → MQTT → Home Assistant
```

**기기와 직접 통신하는 로컬 통합은 아닙니다.** 추출기는 SmartThings 개발자 API나 삼성 계정 토큰을 사용하지 않지만, 공식 앱의 로그인과 클라우드 연결은 필요합니다. 향후 정책 변경 이후의 작동이나 무료 사용을 보장하지 않습니다.

## 확인한 것 / 아직 확인하지 않은 것

2026-11-06에 Apple Silicon Mac mini, 루팅 가능한 Android 17 Google APIs ARM64 에뮬레이터, 공식 SmartThings **1.8.52.23 / 185223010**에서 확인했습니다.

- 앱 DB에서 `powerMeter.power`(W), `energyMeter.energy`(kWh), 원본 측정 시각 추출 성공.
- 연속 샘플의 값과 시각이 갱신됨을 확인. 같은 시각의 기존 HA SmartThings 센서 기록과도 일치했습니다.
- 개발자 API 인증, PSK 추출, Frida, 화면 인식, 기기 삭제·재등록 없이 읽었습니다.
- 추출기는 Mac 기본 Python **3.9.6**에서도 실행했습니다.
- MQTT 브리지는 별도로 구현했고, Paho를 통한 실제 QoS 1 전송을 로컬 테스트 서버로 검증합니다.

**아직 검증하지 않은 부분:** 실제 HA에서 새 MQTT 센서의 장기 운용, 앱 백그라운드·로그인 만료·재부팅 후 자동 갱신, 다른 앱 버전/계정/휴대폰의 DB 호환성, API 요금 정책 적용 이후의 앱 동작. MQTT 브리지는 원래 실측 추출기에 추가한 코드이며 실제 HA 배포 검증까지 마친 것은 아닙니다.

## 필요한 환경

- SmartThings에서 정상 조회되는 EnerZipper Home.
- 개인용 앱 DB를 `adb exec-out cat`으로 읽을 수 있는 루팅 에뮬레이터 또는 개발용 Android 환경.
- Android SDK Platform Tools의 `adb`, Python **3.9 이상**.
- MQTT 브로커와 HA의 MQTT 통합. 두 프로그램이 같은 브로커를 사용해야 합니다.

일반 iPhone이나 루팅하지 않은 Android에서는 앱의 비공개 DB를 이 방법으로 읽을 수 없습니다. `su` 방식만 지원하는 실제 휴대폰도 현재 추출기의 직접 `adb` 읽기와는 다를 수 있습니다. 검증한 대상은 `adb root`가 가능한 개발용 에뮬레이터입니다.

## 1. Android 앱 준비

[에뮬레이터·공식 앱·전용 계정 준비 방법](docs/ANDROID.md)을 먼저 읽으세요. 이미 정상 동작하는 기기를 **새로 등록하거나 삭제할 필요가 없습니다.**

앱에서 에너지퍼 값이 보이는지 확인한 다음 호스트 터미널에서 실행합니다.

```sh
adb devices
adb -s emulator-5556 root
adb -s emulator-5556 wait-for-device
adb -s emulator-5556 shell id
```

`uid=0(root)`를 확인하세요. `adbd cannot run as root in production builds`이면 현재 이미지로는 진행할 수 없습니다. 앱을 열어 로그인 상태와 에너지퍼 값 갱신을 확인합니다. `emulator-5556`은 예시이므로 실제 `adb devices` 출력으로 바꾸세요.

## 2. 추출만 먼저 검증

```sh
git clone https://github.com/DopingDeveloper/EnerZipperHomeHA.git
cd EnerZipperHomeHA
python3 read_enerzipper_cache.py --serial emulator-5556 --samples 3 --interval 30
```

`adb`가 PATH에 없으면 `--adb /absolute/path/to/sdk/platform-tools/adb`를 추가합니다. 에너지퍼의 앱 표시 이름을 변경했다면 `--device-label '실제 기기 이름'`을 지정합니다. **name 또는 label이 정확히 일치하는 기기 하나**만 허용합니다.

합성 출력 예시(실제 사용자 데이터가 아닙니다):

```json
{
  "power": {"value": 123.4, "unit": "W", "source_updated_at": "2026-01-01T00:00:00Z", "age_seconds": 12, "stale": false},
  "energy": {"value": 1234.567, "unit": "kWh", "source_updated_at": "2026-01-01T00:00:00Z", "age_seconds": 12, "stale": false},
  "captured_at": "2026-01-01T00:00:12Z",
  "reader_status": "ok"
}
```

값만 보지 말고 `source_updated_at`이 계속 전진하는지 확인하세요. 앱 캐시가 몇십 초 늦는 것은 실험에서도 관찰됐습니다. 기본적으로 원본 측정 시각이 **180초**보다 오래되면 `stale`입니다. 실패를 0W/0kWh로 바꾸지 않습니다. 단위 불일치·음수·NaN·모호한 기기 선택도 오류입니다.

`--output ./latest.local.json`은 마지막 결과를 권한 `0600`인 파일로 원자적으로 저장합니다. 추출기는 기본적으로 한 번 실행 후 종료하며, `--samples`는 최대 10입니다.

## 3. MQTT 브리지 설정

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.json config.json
```

`config.json`에서 다음을 실제 환경에 맞춥니다.

| 설정 | 의미 |
|---|---|
| `adb` | PATH에 있는 `adb` 또는 실행 파일 절대 경로 |
| `serial` | Android 대상 한 대의 ADB serial |
| `device_label` | 앱 DB의 기기 name 또는 label과 정확히 일치 |
| `poll_interval` | 캐시 읽기 주기(초), 기본 30 |
| `max_age` | 허용할 원본 측정값 나이(초), 기본 180 |
| `instance_id` | 브리지의 고정 식별자. 여러 대면 각각 다르게 지정 |
| `mqtt.host`, `mqtt.port` | 사용할 MQTT 브로커 |
| `mqtt.tls` | TLS 사용 여부. 사용 시 서버 인증서 검증 활성화 |
| `mqtt.ca_file` | 필요한 경우 사용자 CA 파일 경로, 아니면 null |

브로커 로그인 정보는 환경 변수로 전달합니다. 비밀번호를 `config.json`이나 공개 저장소에 쓰지 마세요. 아래 값은 자리표시자입니다.

```sh
export ENERZIPPER_MQTT_USERNAME='YOUR_MQTT_USERNAME'
export ENERZIPPER_MQTT_PASSWORD='YOUR_MQTT_PASSWORD'
```

익명 접속이 허용된 테스트 브로커라면 두 변수 모두 설정하지 않을 수 있습니다. 사용자 이름과 비밀번호 중 하나만 설정하면 시작을 거부합니다. TLS 브로커의 포트는 실제 설정에 맞게 변경하세요(보통 8883).

```sh
# MQTT에 접속하지 않고 현재 앱 값만 읽기
python enerzipper_bridge.py --config config.json --dry-run

# 게시할 HA 자동 검색 설정만 보기 (연결하지 않음)
python enerzipper_bridge.py --config config.json --print-discovery

# 계속 실행
python enerzipper_bridge.py --config config.json
```

`--once`는 MQTT에 한 샘플을 게시한 뒤 종료하는 진단 옵션입니다. 종료 시 availability가 offline이 되므로, 지속적으로 사용하려면 위의 계속 실행 명령을 사용하세요.

Windows는 `.venv\Scripts\Activate.ps1`, 환경 변수는 `$env:ENERZIPPER_MQTT_USERNAME = '...'` 형태를 사용합니다. Android에서 실제 값 추출을 검증한 호스트는 macOS입니다.

## 4. Home Assistant에서 확인

1. **설정 → 기기 및 서비스 → 통합 추가 → MQTT**에서 같은 브로커에 연결합니다. 이미 MQTT를 사용 중이면 기존 연결을 유지합니다.
2. MQTT 자동 검색이 켜져 있는지 확인합니다. 기본 discovery prefix는 `homeassistant`입니다.
3. 브리지를 실행하면 **EnerZipper Home (Android cache)** 기기 아래 두 센서가 검색됩니다.
   - **Power**: W, `device_class: power`, `state_class: measurement`
   - **Energy**: kWh, `device_class: energy`, `state_class: total_increasing`
4. 각 센서의 `source_updated_at` 속성과 실제 스마트싱스 표시값을 비교합니다. 생성된 entity_id는 HA에서 확인하세요. 기존 SmartThings 센서 이름을 덮어쓰지 않습니다.
5. 기존 센서와 병행 검증한 뒤 에너지 대시보드·일간/월간 유틸리티 미터의 입력을 전환합니다. **같은 전력량을 두 센서에서 동시에 합산하지 마세요.** 이 저장소는 기존 자동화나 통계를 자동으로 변경하지 않습니다.

MQTT discovery, 상태 토픽, 시작/종료 및 장애 처리는 [운용 문서](docs/OPERATIONS.md)를 참고하세요. HA YAML 수정이나 재시작은 필요하지 않습니다.

## 5. 검증과 종료

```sh
python read_enerzipper_cache.py --self-test
python -m unittest discover -s tests -v
```

테스트는 합성 SQLite와 로컬 loopback MQTT 서버만 사용합니다. 개인 계정, Android 또는 실제 HA는 필요하지 않습니다. [검증 범위](docs/DESIGN.md#검증-범위)를 참고하세요.

브리지는 **Ctrl+C**로 종료합니다. 에뮬레이터도 종료하려면 별도로 실행합니다.

```sh
adb -s emulator-5556 emu kill
```

브리지는 앱/에뮬레이터를 자동 실행하거나, 재등록하거나, Android 동결·보안 설정을 변경하지 않습니다. 선택적 macOS 자동 실행 예제와 제거 절차는 [운용 문서](docs/OPERATIONS.md#macos-자동-실행선택)를 참고하세요.

## 참고 자료

- [Android AVD 관리](https://developer.android.com/studio/run/managing-avds)
- [Android Debug Bridge](https://developer.android.com/tools/adb)
- [Home Assistant MQTT](https://www.home-assistant.io/integrations/mqtt/)
- [Home Assistant MQTT Sensor](https://www.home-assistant.io/integrations/sensor.mqtt/)
- [Eclipse Paho Python](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html)
- [SmartThings API 정책 안내](https://blog.smartthings.com/smartthings-updates/smartthings-api-additional-details-for-developers-and-business-partners/)

Samsung/SmartThings 또는 EnerZipper의 공식 프로젝트가 아닙니다. MIT License.
