import Foundation

struct MailMessage: Codable, Identifiable, Hashable {
    var id: String
    var sender: String
    var email: String
    var mailbox: String
    var subject: String
    var date: Double
    var received: Double
    var color: String
    var unread: Bool
    var starred: Bool?
    var expectedKeep: Bool
    var decision: String?
    var seen: Bool?
    var otp: OTP?
    var body: String?
    var html: String?
    var conversation: [MailPart]?
    var attachments: [Attachment]?
    var timestamp: Date { Date(timeIntervalSince1970: date) }
}
struct OTP: Codable, Hashable { let code: String; let expires: Double }
struct MailPart: Codable, Hashable {
    var sender: String
    var email: String
    var sent: String
    var to: String
    var subject: String
    var body: String
    var html: String
}
struct Attachment: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let mime: String
    let size: Int
    let preview: String?
    let inline: Bool
}
struct MailSnapshot: Codable {
    let lastSyncedAt: Double?
    let messages: [MailMessage]
    let csrf: String
    let error: String?
    let ownAddresses: [String]
    let account: String?
}
struct SearchResult: Codable { let messages: [MailMessage]; let nextPage: String? }
struct ReplyResult: Codable { let replies: [MailPart] }
struct PushStatus: Codable { let configured: Bool }
