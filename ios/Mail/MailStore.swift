import SwiftUI

@MainActor final class MailStore: ObservableObject {
    let api = MailAPI()
    @Published var lastSyncedAt = UserDefaults.standard.object(forKey:"lastSyncedAt") as? Double
    @Published var signedIn = false
    @Published var messages: [MailMessage] = []
    @Published var searchResults: [MailMessage] = []
    @Published var searchText = ""
    @Published var submittedSearch = ""
    @Published var nextPage: String?
    @Published var searching = false
    @Published var error: String?
    @Published var path: [String] = []
    @Published var ownAddresses: [String] = []
    @Published var starring: Set<String> = []
    private var searchVersion = 0
    private let cache = FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("mail-cache.json")
    init() {
        api.login = CredentialStore.load()
        signedIn = api.login != nil
        if signedIn, let data = try? Data(contentsOf:cache), let saved = try? JSONDecoder().decode([MailMessage].self,from:data) { messages = saved }
    }
    func signIn(username: String, password: String) async {
        api.login = Login(username:username,password:password)
        do {
            let state = try await api.handshake()
            try CredentialStore.save(api.login!)
            signedIn = true; apply(state); error = nil
        } catch { self.error = error.localizedDescription; api.login = nil }
    }
    func refresh(sync: Bool = false) async {
        guard signedIn else { return }
        do {
            if sync { _ = try await api.request("/api/sync",body:[:]) }
            apply(try await api.state())
        } catch { self.error = error.localizedDescription }
    }
    func apply(_ state: MailSnapshot) {
        if let timestamp = state.lastSyncedAt {
            lastSyncedAt = timestamp
            UserDefaults.standard.set(timestamp, forKey:"lastSyncedAt")
        }
        messages = state.messages
        ownAddresses = state.ownAddresses + [state.account ?? ""]
        error = state.error
        try? FileManager.default.createDirectory(at:cache.deletingLastPathComponent(),withIntermediateDirectories:true)
        if let data = try? JSONEncoder().encode(messages) { try? data.write(to:cache,options:[.atomic,.completeFileProtection]) }
    }
    func search(more: Bool = false) async {
        let query = searchText.trimmingCharacters(in:.whitespacesAndNewlines)
        searchVersion += 1; let version = searchVersion
        guard !query.isEmpty else { submittedSearch = ""; searchResults=[]; nextPage=nil; searching=false; return }
        searching = true
        do {
            let result = try await api.search(query,page:more ? nextPage : nil)
            guard version == searchVersion, searchText.trimmingCharacters(in:.whitespacesAndNewlines) == query else { return }
            submittedSearch = query
            if more {
                let ids = Set(searchResults.map(\.id)); searchResults += result.messages.filter { !ids.contains($0.id) }
            } else { searchResults = result.messages }
            nextPage = result.nextPage; error = nil
        } catch { if version == searchVersion { self.error = error.localizedDescription } }
        if version == searchVersion { searching = false }
    }
    func loadMessage(_ id: String) async throws -> MailMessage {
        let directory = cache.deletingLastPathComponent().appendingPathComponent("messages")
        let filename = Data(id.utf8).base64EncodedString().replacingOccurrences(of:"/",with:"_")
        let file = directory.appendingPathComponent(filename)
        do {
            let message = try await api.message(id)
            try? FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
            if let data = try? JSONEncoder().encode(message) { try? data.write(to:file,options:[.atomic,.completeFileProtection]) }
            return message
        } catch {
            if let data = try? Data(contentsOf:file), let message = try? JSONDecoder().decode(MailMessage.self,from:data) { return message }
            throw error
        }
    }
    func toggleStar(_ message: MailMessage) async {
        guard starring.insert(message.id).inserted else { return }
        defer { starring.remove(message.id) }
        do {
            let data = try await api.request("/api/star",body:["id":message.id,"starred":!(message.starred ?? false)])
            struct Result: Decodable { let starred: Bool }
            let result = try JSONDecoder().decode(Result.self,from:data)
            if let i = messages.firstIndex(where:{$0.id == message.id}) { messages[i].starred = result.starred }
            if let i = searchResults.firstIndex(where:{$0.id == message.id}) { searchResults[i].starred = result.starred }
            error = nil
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            if let data = try? JSONEncoder().encode(messages) {
                try? data.write(to:cache,options:[.atomic,.completeFileProtection])
            }
        } catch {
            self.error = error.localizedDescription
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
    }
    func read(_ id: String) async {
        do {
            _ = try await api.request("/api/read",body:["id":id])
            if let i = messages.firstIndex(where:{$0.id == id}) { messages[i].unread = false }
            if let i = searchResults.firstIndex(where:{$0.id == id}) { searchResults[i].unread = false }
        } catch { self.error = error.localizedDescription }
    }
}
