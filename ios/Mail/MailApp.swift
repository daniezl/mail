import SwiftUI
import UserNotifications

@main struct MailApp: App {
    @UIApplicationDelegateAdaptor(PushCoordinator.self) private var push
    @StateObject private var store = MailStore()
    @Environment(\.scenePhase) private var phase
    var body: some Scene {
        WindowGroup {
            Group {
                if store.signedIn { InboxView() } else { LoginView() }
            }
            .environmentObject(store)
            .task(id: store.signedIn) {
                guard store.signedIn else { return }
                await push.prepare(api: store.api)
                openNotification()
            }
            .task(id: push.token) {
                if store.signedIn { await push.prepare(api: store.api) }
            }
            .onChange(of: push.pendingMessage) { _, _ in openNotification() }
            .task(id: "\(phase == .active)-\(store.signedIn)") {
                guard phase == .active, store.signedIn else { return }
                await store.refresh(sync:true,announce:true)
                while !Task.isCancelled {
                    do { try await Task.sleep(for:.seconds(30)) } catch { return }
                    await store.refresh()
                    await push.prepare(api: store.api)
                }
            }
        }
    }
    private func openNotification() {
        guard store.signedIn, let id = push.pendingMessage else { return }
        store.path = [id]
        push.pendingMessage = nil
    }
}

// Keep the pending tap until login completes, including a cold launch.
@MainActor final class PushCoordinator: NSObject, ObservableObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    @Published var token: String?
    @Published var pendingMessage: String?
    private var uploadedToken: String?
    private var registering = false

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func prepare(api: MailAPI) async {
        guard api.login != nil, !registering else { return }
        registering = true
        defer { registering = false }
        let center = UNUserNotificationCenter.current()
        var settings = await center.notificationSettings()
        if settings.authorizationStatus == .notDetermined {
            _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge])
            settings = await center.notificationSettings()
        }
        guard [.authorized, .provisional, .ephemeral].contains(settings.authorizationStatus) else { return }
        UIApplication.shared.registerForRemoteNotifications()
        guard let token, token != uploadedToken else { return }
        #if DEBUG
        let environment = "development"
        #else
        let environment = "production"
        #endif
        do {
            _ = try await api.request("/api/push/register", body: ["token": token, "environment": environment])
            uploadedToken = token
        } catch {
            // A later foreground refresh retries without exposing the device token.
        }
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        token = deviceToken.map { String(format: "%02x", $0) }.joined()
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list, .sound])
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        let id = response.notification.request.content.userInfo["messageId"] as? String
        Task { @MainActor in
            self.pendingMessage = id
            completionHandler()
        }
    }
}
