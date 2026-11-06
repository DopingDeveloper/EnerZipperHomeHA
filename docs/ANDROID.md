# Android 및 계정 준비

## 검증한 구성

- Apple Silicon Mac mini / macOS에서 Android Emulator 실행.
- Android 17 Google APIs ARM64 개발용 이미지, `adb root` 성공.
- 공식 SmartThings 1.8.52.23 (185223010).
- 로그인한 계정에 이미 등록된 EnerZipper Home을 조회.

위 조합에서 앱의 값을 읽는 데 성공한 것이며 모든 이미지·앱 버전에 대한 호환성 보장은 아닙니다. 다른 버전은 한 번 읽기와 여러 샘플의 시각 갱신부터 검증하세요.

## 1. 에뮬레이터

1. [공식 Android Studio](https://developer.android.com/studio)를 설치하고 SDK Manager에서 Android Emulator와 Platform Tools를 설치합니다. SDK와 시스템 이미지 약관은 사용자가 확인하고 동의합니다.
2. Device Manager에서 ARM64 가상 기기를 만듭니다. Apple Silicon에서는 ARM64 이미지를 사용합니다.
3. **Google Play 이미지와 Google APIs 개발용 이미지를 구분합니다.** Google Play 이미지는 release 서명 때문에 `adb root`가 지원되지 않습니다. 이 실험에서는 Google APIs 개발용 이미지를 사용했습니다. AOSP 개발용 이미지도 root를 지원할 수 있지만 Google 서비스가 없어 SmartThings 호환성을 별도로 확인해야 합니다.
4. 가상 기기를 실행하고 `adb devices`로 serial을 확인합니다.
5. `adb -s SERIAL root`, `adb -s SERIAL wait-for-device`, `adb -s SERIAL shell id`로 root가 가능한지 확인합니다.

이미지가 root를 허용하지 않으면 그 이미지로 앱 DB를 읽을 수 없습니다. 이 저장소는 Android root 취약점, Magisk 설치 또는 보안 설정 변경을 자동화하지 않습니다.

## 2. 공식 SmartThings 설치

root 가능한 Google APIs 이미지에는 Play Store가 없을 수 있습니다. 검증 때는 **별도의 Google Play 에뮬레이터에서 공식 SmartThings를 설치하고, 앱 APK만 root 가능한 에뮬레이터로 복사**했습니다. 삼성/Google 계정 데이터나 앱 DB를 복사한 것이 아닙니다.

기본 절차:

1. 별도의 Google Play 지원 AVD를 만들어 Play Store에서 Samsung Electronics의 SmartThings를 설치합니다.
2. 호스트에서 아래 명령으로 APK 경로를 확인합니다. `PLAY_SERIAL`은 해당 AVD의 실제 serial입니다.

   ```sh
   adb -s PLAY_SERIAL shell pm path com.samsung.android.oneconnect
   ```

3. 반환된 `package:` 접두사를 제외한 **모든 APK 경로**를 `adb -s PLAY_SERIAL pull`로 개인 임시 폴더에 복사합니다. base APK 외에 split APK가 반환되면 함께 복사합니다.
4. root AVD로 설치합니다.

   ```sh
   # 반환된 파일이 base.apk 하나일 때
   adb -s ROOT_SERIAL install -r /your/private/apks/base.apk

   # 여러 APK일 때: 반환된 base 및 모든 split APK를 함께 지정
   adb -s ROOT_SERIAL install-multiple -r /your/private/apks/base.apk /your/private/apks/split_example.apk
   ```

5. root AVD에서 **직접 로그인하고 2단계 인증을 완료**합니다. 이후 Play Store AVD는 꺼도 됩니다.

알 수 없는 APK 배포처 대신 자신이 공식 스토어에서 설치한 앱을 사용하세요. 앱 업데이트 때 split/서명/Android 버전 호환성이 달라질 수 있습니다. 이 저장소에 APK는 포함하지 않습니다.

## 3. 전용 삼성 계정

주 계정을 에뮬레이터에 계속 두는 대신 전용 계정을 사용하려면:

1. 별도의 삼성 계정을 만들고 2단계 인증을 설정합니다.
2. 기존 SmartThings 집에서 그 계정을 멤버로 초대합니다.
3. 에뮬레이터의 SmartThings에 전용 계정으로 로그인합니다.
4. 초대한 집에서 에너지퍼가 보이는지, 전력·누적량이 갱신되는지 확인합니다.
5. 추출기를 다시 실행해서 대상 기기와 원본 측정 시각을 검증합니다.

초대 멤버로 동일한 캐시를 읽는 경로는 아직 실측하지 않았습니다. 집 멤버가 에너지퍼 하나만 읽을 수 있다고 가정하지 말고 실제 공유 권한을 확인하세요. 필요한 기기에 접근하지 못하면 주 계정에서 값을 읽었던 결과가 새 계정에도 그대로 적용된다고 볼 수 없습니다.

**기존 에너지퍼를 삭제·이전·재등록하지 마세요.** 로그인된 앱이 클라우드 측정값을 받으면 되는 방법입니다. 에어컨 PSK 추출이나 LocalThings OCF 인증과는 별개입니다.

## 4. 캐시 갱신 검증

앱을 열어둔 상태에서 먼저 다음을 확인합니다.

```sh
python3 read_enerzipper_cache.py --adb /path/to/adb --serial ROOT_SERIAL --samples 5 --interval 30
```

- `reader_status`가 `ok`여야 합니다.
- 전력 수치가 같더라도 `source_updated_at`이 전진하는지 봅니다.
- 단순히 추출 시각 `captured_at`이 바뀌는 것은 새 측정값의 증거가 아닙니다.
- 앱을 백그라운드로 보내거나 호스트를 재부팅한 뒤에도 따로 반복 확인해야 합니다.

이 코드는 앱을 깨우거나 강제 종료하거나 동결 상태를 변경하지 않습니다. 갱신이 멈추면 `stale`로 판단하며, 앱이 항상 백그라운드에서 갱신한다고 보장하지 않습니다.

## 종료

```sh
adb -s ROOT_SERIAL emu kill
```

종료해도 로그인된 가상 기기의 저장 데이터는 남습니다. 계정을 바꾸려면 공식 앱에서 로그아웃/로그인하고 추출을 재검증하세요. SDK·AVD·앱 설치 파일과 데이터 폴더는 이 Git 저장소 밖에서 관리합니다.
