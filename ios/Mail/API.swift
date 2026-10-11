import Foundation
import Security

struct MailError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
struct Login: Codable { let username: String; let password: String }
enum CredentialStore {
    static let service = "com.daniezl.mail.website"
    static func load() -> Login? {
        var result: CFTypeRef?
        let query: [String: Any] = [kSecClass as String:kSecClassGenericPassword, kSecAttrService as String:service,
            kSecReturnData as String:true, kSecMatchLimit as String:kSecMatchLimitOne]
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(Login.self, from:data)
    }
    static func save(_ login: Login) throws {
        let query: [String: Any] = [kSecClass as String:kSecClassGenericPassword, kSecAttrService as String:service]
        SecItemDelete(query as CFDictionary)
        var item = query
        item[kSecValueData as String] = try JSONEncoder().encode(login)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        guard SecItemAdd(item as CFDictionary,nil) == errSecSuccess else { throw MailError(message:"Could not save login.") }
    }
}

@MainActor final class MailAPI {
    static let origin = "https://mail.daniezl.com"
    var login: Login?
    private var csrf = ""
    private let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 45
        config.httpShouldSetCookies = true
        return URLSession(configuration: config)
    }()
    func request(_ path: String, body: [String: Any]? = nil, retry: Bool = true) async throws -> Data {
        guard let login else { throw MailError(message:"Sign in to continue.") }
        var request = URLRequest(url: URL(string:Self.origin + path)!)
        let auth = Data((login.username + ":" + login.password).utf8).base64EncodedString()
        request.setValue("Basic " + auth, forHTTPHeaderField:"Authorization")
        if let body {
            request.httpMethod = "POST"
            request.httpBody = try JSONSerialization.data(withJSONObject:body)
            request.setValue("application/json", forHTTPHeaderField:"Content-Type")
            request.setValue(Self.origin, forHTTPHeaderField:"Origin")
            request.setValue(csrf, forHTTPHeaderField:"X-CSRF-Token")
        }
        let (data, response) = try await session.data(for:request)
        guard let http = response as? HTTPURLResponse else { throw MailError(message:"Connection failed.") }
        if http.statusCode == 401 && retry && path != "/" {
            _ = try await handshake()
            return try await self.request(path, body:body, retry:false)
        }
        guard (200..<300).contains(http.statusCode) else {
            if http.statusCode == 401 { throw MailError(message:"Check your website username and password.") }
            let error = (try? JSONSerialization.jsonObject(with:data)) as? [String:Any]
            throw MailError(message:error?["error"] as? String ?? "Could not load mail. Try again.")
        }
        return data
    }
    @discardableResult func handshake() async throws -> MailSnapshot {
        _ = try await request("/", retry:false)
        let state = try JSONDecoder().decode(MailSnapshot.self, from:await request("/api/state",retry:false))
        csrf = state.csrf
        return state
    }
    func state() async throws -> MailSnapshot {
        let result = try JSONDecoder().decode(MailSnapshot.self, from:await request("/api/state"))
        csrf = result.csrf
        return result
    }
    func message(_ id: String) async throws -> MailMessage {
        try JSONDecoder().decode(MailMessage.self, from:await request("/api/message?id=" + escaped(id)))
    }
    func search(_ text: String, page: String? = nil) async throws -> SearchResult {
        var body: [String:Any] = ["query":text]
        if let page { body["page"] = page }
        return try JSONDecoder().decode(SearchResult.self, from:await request("/api/search",body:body))
    }
    func replies(_ id: String) async throws -> [MailPart] {
        try JSONDecoder().decode(ReplyResult.self, from:await request("/api/replies",body:["id":id])).replies
    }
    func escaped(_ text: String) -> String { text.addingPercentEncoding(withAllowedCharacters:.alphanumerics)! }
    func attachment(_ a: Attachment, message: String, preview: Bool = false) async throws -> Data {
        try await request("/api/" + (preview ? "attachment-preview" : "attachment") + "?id=" + escaped(message) + "&part=" + escaped(a.id))
    }
}
