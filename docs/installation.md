# Installation

Both apps are distributed as APK files in [`../apps`](../apps) and are installed by sideloading.
They share the package name `com.INI.VRphos`, so only one of them can be installed on a device.

| file | device |
|---|---|
| `VRphos_MetaQuest.apk` | Meta Quest 3 / 3S |
| `VRphos_Android.apk` | Android phone (arm64), e.g. in a Cardboard-style VR viewer |

Check the download with `sha256sum -c SHA256SUMS.txt` inside `apps/`.

## Meta Quest

1. Enable developer mode for the headset (Meta Horizon app on the phone → Devices → Developer mode;
   this needs a free Meta developer account).
2. Connect the headset over USB-C and allow USB debugging in the headset when asked.
3. Install [Android platform tools](https://developer.android.com/tools/releases/platform-tools)
   (`adb`) and run:
   ```bash
   adb devices                              # the headset must show as "device"
   adb install -r apps/VRphos_MetaQuest.apk
   ```
   (Alternatively drag the APK onto [SideQuest](https://sidequestvr.com) or the Meta Quest Developer Hub.)
4. In the headset: *Library → Unknown sources → VRphos*.
5. On first start, allow **headset camera** access (needed for *Headset camera* input) and, if you
   want to use tool packs from a shared folder, file access.

## Android phone

1. Enable developer options and USB debugging (Settings → About phone → tap *Build number* 7×;
   then Settings → System → Developer options → USB debugging).
2. Install:
   ```bash
   adb install -r apps/VRphos_Android.apk
   ```
   or copy the APK to the phone and open it (allow installing from unknown sources).
3. On first start, allow **camera** access.

## Uninstall

```bash
adb uninstall com.INI.VRphos
```

## Signing

The APKs are release builds signed with a development key; Android shows this as an app from an
unknown source. If an older VRphos build signed with a different key is installed, `adb install`
fails with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`: uninstall it first.
