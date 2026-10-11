import SwiftUI
import WebKit
import QuickLook

struct ReaderView: View {
    @EnvironmentObject var store: MailStore
    let messageID: String
    @State private var message: MailMessage?
    @State private var replies: [MailPart] = []
    @State private var error: String?
    @State private var previewURL: URL?
    var body: some View {
        ScrollViewReader { scroll in
            ScrollView {
                VStack(alignment:.leading,spacing:22) {
                    if let message {
                        HStack { Text(message.sender).font(.headline); Spacer(); Text(message.timestamp,format:.dateTime.month(.abbreviated).day().hour().minute()).font(.caption).foregroundStyle(.secondary) }
                        Text(message.subject).font(.largeTitle.bold())
                        let parts = message.conversation ?? []
                        // Earlier messages remain above the opened message.
                        ForEach(Array(parts.enumerated()).dropFirst().reversed(),id:\.offset) { _,part in
                            PartCard(part:part,initiallyOpen:false)
                        }
                        VStack(alignment:.leading,spacing:22) {
                            PartCard(part:parts.first ?? MailPart(sender:message.sender,email:message.email,sent:"",to:message.mailbox,subject:message.subject,body:message.body ?? "",html:message.html ?? ""),initiallyOpen:true)
                            ForEach(message.attachments?.filter { !$0.inline } ?? []) { attachment in
                                AttachmentCard(attachment:attachment,messageID:messageID) { url in previewURL=url }
                            }
                            ForEach(Array(replies.enumerated()),id:\.offset) { _,part in PartCard(part:part,initiallyOpen:false) }
                        }
                        .id("opened-message")
                        .task(id:message.id) {
                            await Task.yield()
                            guard !Task.isCancelled else { return }
                            var transaction = Transaction()
                            transaction.disablesAnimations = true
                            withTransaction(transaction) { scroll.scrollTo("opened-message",anchor:.top) }
                        }
                    } else if error == nil { ProgressView().frame(maxWidth:.infinity) }
                    if let error { Button(error + " · Retry") { Task { await load() } }.font(.callout).foregroundStyle(.secondary) }
                }.padding(20)
            }
        }.navigationBarTitleDisplayMode(.inline).quickLookPreview($previewURL)
        .task(id:messageID) { await load() }
    }
    func load() async {
        do {
            message = try await store.loadMessage(messageID); error=nil
            await store.read(messageID)
            replies = try await store.api.replies(messageID)
        } catch { self.error = error.localizedDescription }
    }
}
struct PartCard: View {
    @EnvironmentObject var store: MailStore
    let part: MailPart
    @State private var expanded: Bool
    init(part:MailPart,initiallyOpen:Bool) { self.part=part; _expanded=State(initialValue:initiallyOpen) }
    var recipients: String {
        let pattern = #"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}"#
        let regex = try! NSRegularExpression(pattern:pattern,options:.caseInsensitive)
        let range = NSRange(part.to.startIndex...,in:part.to)
        let all = regex.matches(in:part.to,range:range).compactMap { Range($0.range,in:part.to).map { String(part.to[$0]) } }
        let mine = all.filter { store.ownAddresses.map { $0.lowercased() }.contains($0.lowercased()) }
        return (mine.isEmpty ? all : mine).joined(separator:", ")
    }
    var body: some View {
        VStack(alignment:.leading,spacing:16) {
            Button {
                UISelectionFeedbackGenerator().selectionChanged()
                withAnimation(.easeInOut(duration:0.2)) { expanded.toggle() }
            } label: {
                HStack { Image(systemName:expanded ? "chevron.down" : "chevron.right").font(.caption); Text(part.sender).font(.headline); Spacer(); Text(part.sent).font(.caption).lineLimit(1).foregroundStyle(.secondary) }.contentShape(Rectangle())
            }.buttonStyle(.plain)
            if expanded {
                VStack(alignment:.leading,spacing:4) { Text("From: " + part.email); if !recipients.isEmpty { Text("To: " + recipients) } }.font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                if part.html.isEmpty { Text(part.body).font(.body).textSelection(.enabled) }
                else { HTMLBody(html:part.html) }
            }
        }.padding(16).background(Color(.secondarySystemBackground),in:RoundedRectangle(cornerRadius:16)).overlay(RoundedRectangle(cornerRadius:16).stroke(Color.primary.opacity(0.07)))
    }
}
struct AttachmentCard: View {
    @EnvironmentObject var store: MailStore
    let attachment: Attachment
    let messageID: String
    let open: (URL)->Void
    @State private var image: UIImage?
    @State private var error: String?
    @State private var working=false
    var body: some View {
        Button { Task {
            working=true; defer { working=false }
            do {
                let data=try await store.api.attachment(attachment,message:messageID)
                let directory=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
                try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
                let url=directory.appendingPathComponent((attachment.name as NSString).lastPathComponent)
                try data.write(to:url,options:.completeFileProtection); open(url)
            } catch { self.error="Could not open attachment. Tap to retry." }
        } } label: {
            VStack(alignment:.leading,spacing:12) {
                if let image { Image(uiImage:image).resizable().scaledToFit().frame(maxWidth:.infinity,maxHeight:230).clipped() }
                else { Image(systemName:attachment.preview == "pdf" ? "doc.richtext" : "paperclip").font(.system(size:40)).frame(maxWidth:.infinity,minHeight:120).foregroundStyle(.secondary) }
                HStack { Text(attachment.name).lineLimit(2); Spacer(); if working { ProgressView() } else { Image(systemName:"arrow.up.right.square") } }.font(.callout)
                if let error { Text(error).font(.caption).foregroundStyle(.red) }
            }.padding(16).background(Color(.secondarySystemBackground),in:RoundedRectangle(cornerRadius:16))
        }.buttonStyle(.plain).disabled(working)
        .task { if attachment.preview != nil, let data=try? await store.api.attachment(attachment,message:messageID,preview:true) { image=UIImage(data:data) } }
    }
}
struct HTMLBody: View {
    let html: String
    @State private var height: CGFloat=180
    var body: some View { SafeWebView(html:html,height:$height).frame(height:height) }
}
struct SafeWebView: UIViewRepresentable {
    @Environment(\.colorScheme) private var colorScheme
    let html: String
    @Binding var height: CGFloat
    func makeCoordinator() -> Coordinator { Coordinator(height:$height) }
    func makeUIView(context:Context) -> WKWebView {
        let config=WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.defaultWebpagePreferences.allowsContentJavaScript=false
        let web=WKWebView(frame:.zero,configuration:config)
        web.navigationDelegate=context.coordinator; web.scrollView.isScrollEnabled=false
        web.isOpaque=false;web.backgroundColor = .clear
        context.coordinator.observation=web.scrollView.observe(\.contentSize,options:[.new]) { _,change in
            let size=change.newValue ?? .zero
            Task { @MainActor in if size.height>0 { context.coordinator.height.wrappedValue=size.height } }
        }
        return web
    }
    func updateUIView(_ web:WKWebView,context:Context) {
        let dark = colorScheme == .dark
        guard context.coordinator.lastHTML != html || context.coordinator.lastDark != dark else { return }
        context.coordinator.lastHTML = html
        context.coordinator.lastDark = dark
        web.overrideUserInterfaceStyle = dark ? .dark : .light
        // Email HTML often hard-codes black text. Normalize text and backgrounds
        // together in dark mode so nested tables and signatures remain readable.
        let darkCSS = dark ? """
        html body, html body * {
            color:#ededed !important;
            -webkit-text-fill-color:#ededed !important;
            background-color:transparent !important;
            background-image:none !important;
            text-shadow:none !important;
        }
        html body a, html body a * {
            color:#8ab4f8 !important;
            -webkit-text-fill-color:#8ab4f8 !important;
        }
        """ : ""
        // Table-based email templates often retain a 600px desktop canvas.
        // max-width alone cannot constrain the table's intrinsic minimum width.
        let responsiveCSS = """
        html,body{width:100%!important;min-width:0!important;max-width:100%!important}
        body{-webkit-text-size-adjust:100%}
        body *{box-sizing:border-box;overflow-wrap:anywhere}
        table{width:100%!important;max-width:100%!important;min-width:0!important;table-layout:fixed!important}
        td,th{min-width:0!important;overflow-wrap:anywhere!important;white-space:normal!important}
        div,p,section{max-width:100%!important;min-width:0!important}
        img,svg,video{max-width:100%!important;height:auto!important}
        pre{white-space:pre-wrap!important;overflow-wrap:anywhere!important}
        @media(max-width:480px){
            td[style*="padding-left"],th[style*="padding-left"]{padding-left:12px!important}
            td[style*="padding-right"],th[style*="padding-right"]{padding-right:12px!important}
            td[style*="padding:"],th[style*="padding:"]{padding-left:12px!important;padding-right:12px!important}
        }
        """
        let document="""
        <!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'none'; form-action 'none'; base-uri 'none'"><style>:root{color-scheme:\(dark ? "dark" : "light")}body{color:\(dark ? "#ededed" : "#171717");background:transparent;margin:0;font:17px -apple-system;line-height:1.6;overflow-wrap:anywhere}img,table{max-width:100%!important}img{height:auto}a{color:#538de0}pre{white-space:pre-wrap}\(darkCSS)\(responsiveCSS)</style></head><body>\(html)</body></html>
        """
        web.loadHTMLString(document,baseURL:nil)
    }
    final class Coordinator: NSObject,WKNavigationDelegate {
        var height: Binding<CGFloat>
        var lastHTML: String?
        var lastDark: Bool?
        var observation: NSKeyValueObservation?
        init(height:Binding<CGFloat>) { self.height=height }
        func webView(_ webView:WKWebView,decidePolicyFor action:WKNavigationAction,decisionHandler:@escaping(WKNavigationActionPolicy)->Void) {
            if action.navigationType == .linkActivated {
                if let url=action.request.url, ["https","http","mailto"].contains(url.scheme?.lowercased() ?? "") { UIApplication.shared.open(url) }
                decisionHandler(.cancel)
            } else { decisionHandler(action.request.url?.scheme == "about" ? .allow : .cancel) }
        }
    }
}
