# Mail for iOS

Native SwiftUI client for the existing Mail server. iOS 17+, light/dark appearance follows the system.

The inbox contains only mail, search, and **Show filtered** (off by default). Reading supports collapsed conversation cards, later replies, HTML with JavaScript and remote resources disabled, large attachment previews and Quick Look. Verification-code copying marks the message read and briefly shows a checkmark. Opened messages have protected on-device offline copies. Search queries Gmail through the server and includes archived/filtered mail.

Opening the app and pulling to refresh show **Checking…** while the server completes its Gmail check. A successful check with no new message IDs reveals **No new mail** from left to right, holds for one second, then wipes it away. New mail, failed checks, and cancelled checks do not show that message. The regular 30-second poll stays quiet.

Open `Mail.xcodeproj`, select the Mail scheme and your iPhone. The app uses `https://mail.daniezl.com`; sign in with the website credentials, not the Gmail password. Credentials are stored in this device's Keychain. Gmail and Jev credentials stay on the server. No API keys or personal prompts belong in the app bundle.

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild \
  -project ios/Mail.xcodeproj -scheme Mail \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/mail-ios-build CODE_SIGN_IDENTITY=- build
```

Keep ad-hoc signing enabled for simulator runs: an unsigned build can compile but cannot save the website login in Keychain.

## Push setup

After website sign-in, the app requests notification permission and registers this device with the server. Tapping a notification opens its email, including after a cold launch. Device registration retries on foreground refresh.

Requires an Apple Developer membership, a provisioned App ID `com.daniezl.mail.ios` with Push Notifications enabled, and a token-based APNs key. Select the appropriate signing team in Xcode. Debug builds register for development APNs; Release uses production APNs. On-device signing must match the entitlement.

Set these **private server environment variables** in `/etc/mail.env`, then restart `mail.service`:

```text
MAIL_BACKGROUND_SYNC=1
APNS_KEY_FILE=/opt/mail/.local/apns.p8
APNS_KEY_ID=YOUR_KEY_ID
APNS_TEAM_ID=YOUR_TEAM_ID
APNS_TOPIC=com.daniezl.mail.ios
```

The `.p8` file must be readable only by the service account. Never commit it. Keep encrypted backups separately. See Apple's [APNs registration](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns) and [token-based connection](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns) documentation.

Server polling continues every 30 seconds even when the website/app is closed. Device registration baselines existing cache entries. Only subsequently received, unread inbox mail classified **keep** or **uncertain** is pushed. Hide is never pushed. No history is replayed after reclassification or service restart. Failed requests retry with backoff; APNs-invalid devices are removed. Push payloads contain sender, subject, and message ID, not the body or attachments. Notification previews obey iOS lock-screen settings. APNs delivery timing is controlled by Apple and is not guaranteed.

Real-device notification testing requires the APNs key, valid provisioning, permission from the user, and a new incoming email. Simulator compilation and server unit tests alone do not verify real notification delivery.

## Animation preview (Debug only)

Long-press **Show filtered** → **Preview animation**, or use **Preview animation** on the sign-in screen. Choose 1, 5, or 20 messages; tap Replay to play again. The preview uses the production cleanup animation and haptics with synthetic, memory-only messages. It makes no API requests and does not change mail, classifications, or presentation history. Release builds omit the preview UI.
