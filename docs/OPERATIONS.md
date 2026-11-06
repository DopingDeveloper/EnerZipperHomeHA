# 운용 및 문제 해결

## 시작 순서

1. MQTT 브로커와 HA MQTT 통합을 준비합니다.
2. 개발용 Android 에뮬레이터를 실행하고 `adb root`가 가능한지 확인합니다.
3. SmartThings에 로그인하고 에너지퍼가 갱신되는지 봅니다.
4. 추출기 여러 샘플로 원본 시각 갱신을 확인합니다.
5. 브리지를 시작합니다.

브리지는 Android나 앱을 자동으로 실행하지 않습니다. 앱이 종료·로그아웃됐거나 캐시가 오래되면 availability가 offline이 됩니다. 따라서 정상 호스트 부팅만으로 전체 경로가 자동 복구된다고 가정하면 안 됩니다.

## 상태 로그

브리지는 측정값이나 비밀번호 대신 상태만 기록합니다.

```json
{"event": "sample", "status": "ok"}
{"event": "sample", "status": "stale"}
{"event": "sample_error", "kind": "ReadError"}
```

측정값과 안전한 상세 원인을 확인하려면 직접 추출기를 실행합니다.

```sh
python3 read_enerzipper_cache.py --adb /path/to/adb --serial emulator-5556
```

| 결과 | 확인할 것 |
|---|---|
| `adb_read_failed` | ADB 경로·serial·실행 여부, `uid=0`, 앱 설치 여부 |
| `target_missing_or_ambiguous` | 새 계정의 집 접근, 정확한 label/name, 같은 이름 기기 중복 |
| `measurement_missing_or_ambiguous` | 에너지퍼 화면을 열어 값 수신 확인, 앱 스키마 변경 여부 |
| `unexpected_unit` | W/kWh가 아닌 값이면 자동 변환하지 않고 중단함 |
| `invalid_measurement` | 값/원본 시각 검증 실패. Android와 호스트 시각도 확인 |
| `cache_snapshot_unstable` | 앱 DB가 복사 중 계속 변경됨. 다음 poll에서 재시도 |
| `stale` | 앱·클라우드·에너지퍼의 실제 갱신 문제. 단순히 `max_age`만 크게 늘리지 말 것 |
| DB 관련 Python 오류 | 앱 업데이트로 스키마가 달라졌거나 복사 중 충돌. 원본 DB 수정 금지 |
| HA에서 검색되지 않음 | 같은 MQTT 브로커, discovery prefix·자동 검색, MQTT topic ACL 확인 |

`--dry-run`은 MQTT 접속 없이 앱 값을 읽습니다. `--print-discovery`는 앱/브로커에 접속하지 않고 게시될 설정을 확인합니다. MQTT 연결 문제는 host/port·인증·TLS CA·네트워크와 브로커 로그를 확인하세요. 기본 계속 실행 모드의 MQTT 클라이언트는 연결 실패 후 재연결을 시도합니다. `--once`는 30초 연결 제한을 둡니다.

## 계정 변경·앱 업데이트

공식 앱에서 직접 로그아웃·로그인하고 집을 선택한 뒤 여러 샘플을 다시 검사합니다. 새 계정에서 값이 보이는 것과 장시간 캐시가 갱신되는 것은 각각 확인해야 합니다. 이 저장소는 로그인 자동화나 토큰 추출을 하지 않습니다.

업데이트 후 오류가 나면 실제 DB 구조와 측정값 계약이 바뀌었는지 확인합니다. 읽기를 실패로 표시하는 대신 임의의 행을 선택하거나 다른 기기의 값을 대신 게시하지 마세요. 버전을 검증한 후 이슈에는 앱 버전, Python 버전, 안전한 오류 코드만 공유하세요. DB·APK·계정 토큰을 올리지 마세요.

## macOS 자동 실행(선택)

다음은 **수동 검증을 마친 뒤** 쓸 수 있는 launchd 예시입니다. 설치 명령을 실행하기 전 경로와 설정을 직접 수정하세요. 이 예시는 브리지만 시작하고 에뮬레이터는 시작하지 않습니다. macOS 로그인 세션이 필요하며, 자동 실행·장기 운용은 실제 기기에서 검증하지 않았습니다.

1. 저장소를 자신의 고정 경로에 둡니다(예: `~/Workspace/EnerZipperHomeHA`). venv와 `config.json`을 준비합니다.
2. `examples/run-macos.sh`의 작업 폴더를 실제 경로에 맞추고 개인 사본을 만듭니다.
3. 환경 변수 파일을 `~/.config/enerzipper/mqtt.env`에 만듭니다. 내용은 `ENERZIPPER_MQTT_USERNAME='...'`, `ENERZIPPER_MQTT_PASSWORD='...'` 두 줄입니다. 해당 파일은 shell이 읽으므로 본인이 작성한 파일만 사용하세요.
4. 환경 변수 파일 권한은 `600`, `~/.config/enerzipper` 디렉터리는 `700`, 실행 스크립트는 `700`으로 설정합니다. 비밀번호 파일은 Git 저장소 밖에 둡니다.
5. `examples/io.github.dopingdeveloper.enerzipperhomeha.example.plist`의 **모든 `YOUR_USER`와 경로**를 실제 값으로 바꿔 `~/Library/LaunchAgents/io.github.dopingdeveloper.enerzipperhomeha.plist`로 저장합니다.

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.github.dopingdeveloper.enerzipperhomeha.plist
```

중지하고 자동 실행도 해제하려면:

```sh
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/io.github.dopingdeveloper.enerzipperhomeha.plist
```

launchd로 실행한 경우 Ctrl+C가 아니라 `bootout`으로 중지합니다. `KeepAlive` 설정 때문에 프로세스만 종료하면 다시 시작될 수 있습니다. 부팅 후 에뮬레이터와 앱이 준비되기 전에는 브리지가 읽기 오류를 내고 offline을 유지하는 것이 정상입니다.

## HA 센서 제거

브리지를 먼저 중지합니다. 기본 설정 기준 아래 두 retained discovery 메시지를 MQTT 클라이언트에서 **빈 payload, retain=true**로 게시하면 검색 설정을 제거할 수 있습니다.

```text
homeassistant/sensor/enerzipper_cache_home_meter_power/config
homeassistant/sensor/enerzipper_cache_home_meter_energy/config
```

instance_id 또는 discovery prefix를 바꿨다면 실제 토픽을 사용하세요. 임의로 `homeassistant/#` 전체 retained 메시지를 지우지 마세요. 기존 SmartThings 센서나 에너지 기록 삭제는 이 브리지의 제거 절차에 포함되지 않습니다.
