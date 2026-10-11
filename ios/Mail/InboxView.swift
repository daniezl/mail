import SwiftUI

struct LoginView: View {
    @EnvironmentObject var store: MailStore
    @State private var username = "daniezl"
    @State private var password = ""
    @State private var working = false
    var body: some View {
        VStack(alignment:.leading,spacing:22) {
            Text("Sign in").font(.largeTitle.bold())
            Text("mail.daniezl.com").foregroundStyle(.secondary)
            TextField("Username",text:$username).textContentType(.username).textInputAutocapitalization(.never).autocorrectionDisabled()
            SecureField("Website password",text:$password).textContentType(.password)
            if let error = store.error { Text(error).font(.callout).foregroundStyle(.red) }
            Button { working=true; Task { await store.signIn(username:username,password:password); working=false } } label: {
                Group { if working { ProgressView() } else { Text("Sign in") } }.frame(maxWidth:.infinity)
            }.buttonStyle(.borderedProminent).disabled(working || password.isEmpty)
        }.textFieldStyle(.roundedBorder).padding(32).frame(maxWidth:500)
    }
}
struct InboxView: View {
    @EnvironmentObject var store: MailStore
    @Environment(\.colorScheme) private var colorScheme
    @AppStorage("showFiltered") private var showFiltered = false
    var displayed: [MailMessage] {
        if !store.submittedSearch.isEmpty { return store.searchResults }
        return store.messages.filter { showFiltered || $0.starred == true || $0.expectedKeep || ["keep", "uncertain"].contains($0.decision ?? "") }
    }
    private func rowBackground(_ message: MailMessage) -> Color {
        if message.starred == true { return .blue.opacity(colorScheme == .dark ? 0.25 : 0.14) }
        guard showFiltered, message.decision == "hide" else { return .clear }
        let opacity = colorScheme == .dark ? 0.22 : 0.14
        return .red.opacity(opacity)
    }
    var body: some View {
        NavigationStack(path:$store.path) {
            ScrollViewReader { scroll in
            List {
                ForEach(displayed) { message in
                    StarSwipeRow(message:message,highlight:rowBackground(message),reset:showFiltered)
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                        .id(message.id)
                }
                if store.searching { ProgressView().frame(maxWidth:.infinity) }
                if store.nextPage != nil && !store.submittedSearch.isEmpty {
                    Button("Load more") { Task { await store.search(more:true) } }.disabled(store.searching)
                }
            }
            .listStyle(.plain)
            .onChange(of:showFiltered) { _,_ in
                if let first = displayed.first {
                    var transaction = Transaction()
                    transaction.disablesAnimations = true
                    withTransaction(transaction) { scroll.scrollTo(first.id,anchor:.top) }
                }
            }
            .overlay { if displayed.isEmpty && !store.searching && store.error == nil { Text("No mail").foregroundStyle(.tertiary) } }
            .safeAreaInset(edge:.top,spacing:0) {
                TimelineView(.periodic(from:.now,by:1)) { context in
                    Text(syncStatus(at:context.date))
                        .font(.caption).foregroundStyle(.secondary)
                        .frame(maxWidth:.infinity,alignment:.leading)
                        .padding(.horizontal,20).padding(.vertical,6)
                }.background(Color(.systemBackground))
            }
            .toolbar {
                if #available(iOS 26.0, *) {
                    DefaultToolbarItem(kind:.search,placement:.bottomBar)
                    ToolbarSpacer(.fixed,placement:.bottomBar)
                    ToolbarItem(placement:.bottomBar) { filteredButton }
                } else {
                    ToolbarItem(placement:.topBarTrailing) { filteredButton }
                }
            }
            .searchable(text:$store.searchText,prompt:"Search mail")
            .onSubmit(of:.search) { Task { await store.search() } }
            .onChange(of:store.searchText) { _,value in if value.isEmpty { Task { await store.search() } } }
            .refreshable { await store.refresh(sync:true) }
            .navigationDestination(for:String.self) { ReaderView(messageID:$0) }
            }
        }
    }
    private func syncStatus(at date: Date) -> String {
        guard let timestamp = store.lastSyncedAt else { return "Not synced yet" }
        let seconds = max(0,Int(date.timeIntervalSince1970 - timestamp))
        if seconds < 60 { return "Synced \(seconds)s ago" }
        if seconds < 3600 { return "Synced \(seconds / 60)m ago" }
        if seconds < 86400 { return "Synced \(seconds / 3600)h ago" }
        return "Synced \(seconds / 86400)d ago"
    }
    private var filteredButton: some View {
        Button {
            UISelectionFeedbackGenerator().selectionChanged()
            showFiltered.toggle()
        } label: {
            Image(systemName:"line.3.horizontal.decrease")
                .foregroundStyle(showFiltered ? Color.blue : Color.primary)
        }
        .accessibilityLabel("Show filtered")
        .accessibilityValue(showFiltered ? "On" : "Off")
    }

}
/// A flat action surface revealed behind the message, with a full-swipe threshold.
struct StarSwipeRow: View {
    @EnvironmentObject var store: MailStore
    let message: MailMessage
    let highlight: Color
    let reset: Bool
    @State private var offset: CGFloat = 0
    @State private var dragStart: CGFloat?
    @State private var horizontal: Bool?
    @State private var armed = false
    @State private var suppressOpenUntil = Date.distantPast
    private let actionWidth: CGFloat = 84
    private var working: Bool { store.starring.contains(message.id) }
    private var title: String { message.starred == true ? "Unstar" : "Star" }
    private var symbol: String { message.starred == true ? "star.slash.fill" : "star.fill" }
    var body: some View {
        if #available(iOS 18.0, *) {
        ZStack(alignment:.leading) {
            GeometryReader { geometry in
                let threshold = min(110, max(80, geometry.size.width * 0.24))
                ZStack(alignment:.leading) {
                    Color.blue.opacity(armed ? 1 : 0.4)
                        .animation(.easeOut(duration:0.12),value:armed)
                    Button(action:perform) {
                        VStack(spacing:7) {
                            Image(systemName:symbol).font(.system(size:24,weight:.medium))
                            Text(title).font(.caption.weight(.semibold))
                        }.foregroundStyle(.white)
                            .frame(width:max(actionWidth,offset),height:geometry.size.height)
                            .contentShape(Rectangle())
                    }.buttonStyle(.plain).disabled(working)
                }
                .frame(width:max(0,offset),height:geometry.size.height)
                .clipped().frame(maxWidth:.infinity,alignment:.leading)
                .accessibilityHidden(offset == 0)
                .overlay {
                    rowContent
                        .offset(x:offset)
                        .gesture(HorizontalMailPan(canStart:!working,canClose:offset > 0) { translation,state in
                            suppressOpenUntil = Date().addingTimeInterval(0.4)
                            switch state {
                            case .began, .changed:
                                if dragStart == nil { dragStart=offset; horizontal=true }
                                offset = max(0,min(geometry.size.width,(dragStart ?? 0)+translation))
                                let crossed = offset >= threshold
                                if crossed && !armed { UIImpactFeedbackGenerator(style:.medium).impactOccurred() }
                                armed = crossed
                            case .ended:
                                if offset >= threshold { perform() }
                                else { springBack() }
                            case .cancelled, .failed:
                                springBack()
                            default: break
                            }
                        })
                }
            }
        }
        .frame(height:rowHeight)
        .clipped()
        .animation(horizontal == true ? nil : .spring(response:0.42,dampingFraction:0.72),value:offset)
        .onChange(of:reset) { _,_ in offset=0; armed=false; horizontal=nil; dragStart=nil }
        .accessibilityAction(named:Text(title),perform)
            } else {
            rowContent.swipeActions(edge:.leading,allowsFullSwipe:true) {
                Button(action:perform) { Label(title,systemImage:symbol) }.tint(.blue)
            }
        }
    }
    @ScaledMetric(relativeTo:.body) private var rowHeight: CGFloat = 108
    private var rowContent: some View {
        HStack(spacing:12) {
            Button {
                // A drag can end after the row has sprung back to zero. Do not
                // let that release become a tap on the underlying mail button.
                guard horizontal == nil, Date() >= suppressOpenUntil else { return }
                if offset != 0 { springBack() }
                else { store.path.append(message.id) }
            } label: { MailRow(message:message) }.buttonStyle(.plain)
            if let otp=message.otp,otp.expires > Date().timeIntervalSince1970 {
                CopyCodeButton(messageID:message.id,code:otp.code)
            }
        }
        .padding(.horizontal,20).padding(.vertical,10)
        .frame(maxWidth:.infinity,maxHeight:.infinity)
        .background(Color(uiColor:.systemBackground).overlay(highlight))
    }
    private func springBack() {
        horizontal=nil; dragStart=nil
        // UIKit gesture callbacks can inherit a transaction with animations
        // disabled. Explicitly animate the row and the revealed action together.
        var transaction = Transaction(animation:.spring(response:0.42,dampingFraction:0.72))
        transaction.disablesAnimations = false
        withTransaction(transaction) { offset=0; armed=false }
    }
    private func perform() {
        guard !working else { return }
        UIImpactFeedbackGenerator(style:.light).impactOccurred()
        springBack()
        Task { await store.toggleStar(message) }
    }
}
// Reject vertical movement before recognizing a pan, so the List keeps its
// native scrolling gesture. A SwiftUI DragGesture recognizes both axes first.
@available(iOS 18.0, *)
private struct HorizontalMailPan: UIGestureRecognizerRepresentable {
    let canStart: Bool
    let canClose: Bool
    let action: (CGFloat,UIGestureRecognizer.State)->Void
    func makeCoordinator(converter:CoordinateSpaceConverter) -> Coordinator { Coordinator() }
    func makeUIGestureRecognizer(context:Context) -> UIPanGestureRecognizer {
        let pan = UIPanGestureRecognizer()
        pan.maximumNumberOfTouches = 1
        pan.cancelsTouchesInView = true
        pan.delegate = context.coordinator
        context.coordinator.canStart=canStart; context.coordinator.canClose=canClose
        return pan
    }
    func updateUIGestureRecognizer(_ pan:UIPanGestureRecognizer,context:Context) {
        context.coordinator.canStart=canStart; context.coordinator.canClose=canClose
    }
    func handleUIGestureRecognizerAction(_ pan:UIPanGestureRecognizer,context:Context) {
        action(pan.translation(in:pan.view).x,pan.state)
    }
    final class Coordinator: NSObject,UIGestureRecognizerDelegate {
        var canStart=true
        var canClose=false
        func gestureRecognizerShouldBegin(_ gesture:UIGestureRecognizer) -> Bool {
            guard canStart,let pan=gesture as? UIPanGestureRecognizer else { return false }
            let velocity=pan.velocity(in:pan.view)
            return abs(velocity.x) > abs(velocity.y)*1.3 && (velocity.x > 0 || canClose)
        }
    }
}

struct MailRow: View {
    let message: MailMessage
    var body: some View {
        VStack(alignment:.leading,spacing:8) {
            HStack(spacing:8) {
                RoundedRectangle(cornerRadius:2).fill(Color(hex:message.color)).frame(width:3,height:18)
                Text(message.sender).font(.headline).lineLimit(1)
                Spacer(minLength:4)
                if message.unread { Circle().fill(.blue).frame(width:7,height:7).accessibilityLabel("Unread") }
                Text(message.timestamp,format:.dateTime.month(.abbreviated).day()).font(.caption).foregroundStyle(.secondary)
            }
            Text(message.subject).font(.body).foregroundStyle(message.unread ? .primary : .secondary).lineLimit(2)
        }.frame(maxWidth:.infinity,alignment:.leading).contentShape(Rectangle())
    }
}
struct CopyCodeButton: View {
    @EnvironmentObject var store: MailStore
    let messageID: String
    let code: String
    @State private var copied = false
    var body: some View {
        Button {
            UIPasteboard.general.string = code
            copied = true
            Task { await store.read(messageID) }
        } label: {
            Image(systemName:copied ? "checkmark" : "doc.on.doc").frame(width:40,height:44)
        }.buttonStyle(.plain).foregroundStyle(.blue).accessibilityLabel(copied ? "Copied" : "Copy verification code")
        .task(id:copied) {
            guard copied else { return }
            do { try await Task.sleep(for:.seconds(2)); copied=false } catch {}
        }
    }
}
extension Color {
    init(hex:String) {
        let value = UInt(hex.trimmingCharacters(in:CharacterSet(charactersIn:"#")),radix:16) ?? 0x959995
        self.init(.sRGB,red:Double((value>>16)&255)/255,green:Double((value>>8)&255)/255,blue:Double(value&255)/255,opacity:1)
    }
}
