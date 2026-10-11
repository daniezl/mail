import SwiftUI

struct LoginView: View {
    @EnvironmentObject var store: MailStore
    @State private var username = "daniezl"
    @State private var password = ""
    @State private var working = false
    #if DEBUG
    @State private var showAnimationPreview = false
    #endif
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
            #if DEBUG
            Button("Preview animation") { showAnimationPreview=true }
                .sheet(isPresented:$showAnimationPreview) { FilterAnimationPreview() }
            #endif
        }.textFieldStyle(.roundedBorder).padding(32).frame(maxWidth:500)
    }
}
struct InboxView: View {
    @EnvironmentObject var store: MailStore
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @StateObject private var cleanup = InboxCleanup()
    #if DEBUG
    @State private var showAnimationPreview = false
    #endif
    @State private var rowFrames: [String:CGRect] = [:]
    @State private var filterFrame: CGRect = .zero
    @State private var inboxFrame: CGRect = .zero
    @State private var filterEntrance = 1.0
    @State private var filterEntranceTask: Task<Void,Never>?
    @ScaledMetric(relativeTo:.body) private var cleanupRowHeight: CGFloat = 108
    @ScaledMetric(relativeTo:.callout) private var refreshNoticeHeight: CGFloat = 36
    private enum ScrollAnchor: Hashable { case refreshNotice }
    @AppStorage("showFiltered") private var showFiltered = false
    var displayed: [MailMessage] {
        if !store.submittedSearch.isEmpty { return store.searchResults }
        return store.messages.filter { cleanup.previewIDs.contains($0.id) || showFiltered || $0.starred == true || $0.expectedKeep || ["keep", "uncertain"].contains($0.decision ?? "") }
    }
    private func rowBackground(_ message: MailMessage) -> Color {
        if message.starred == true { return .blue.opacity(colorScheme == .dark ? 0.25 : 0.14) }
        guard message.decision == "hide",
              showFiltered || cleanup.previewIDs.contains(message.id) else { return .clear }
        let opacity = colorScheme == .dark ? 0.22 : 0.14
        return .red.opacity(opacity)
    }
    var body: some View {
        NavigationStack(path:$store.path) {
            ScrollViewReader { scroll in
            InboxRows {
                // Keep this space even when the notice is hidden, so the first
                // divider stays put. The notice scrolls with the mail list.
                Color.clear.frame(height:refreshNoticeHeight)
                    .overlay(alignment:.leading) {
                        RefreshNotice(feedback:$store.refreshFeedback)
                            .fixedSize(horizontal:false,vertical:true)
                    }
                    .padding(.bottom,22)
                    .zIndex(1)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
                    .id(ScrollAnchor.refreshNotice)
                ForEach(displayed) { message in
                    StarSwipeRow(message:message,highlight:rowBackground(message),reset:showFiltered)
                        .modifier(MailRowDivider(showTop:message.id == displayed.first?.id))
                        .background { GeometryReader { geo in
                            Color.clear.preference(key:MailRowFrames.self,value:[message.id:geo.frame(in:.global)])
                        } }
                        .modifier(CleanupRowMotion(progress:cleanup.progress,
                            delay:cleanup.delays[message.id]))
                        .frame(height:cleanup.slotHeight(message.id,normal:cleanupRowHeight))
                        .clipped()
                        .allowsHitTesting(cleanup.delays[message.id] == nil)
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                        .id(message.id)
                }
                if store.searching { ProgressView().frame(maxWidth:.infinity) }
                if store.nextPage != nil && !store.submittedSearch.isEmpty {
                    Button("Load more") { Task { await store.search(more:true) } }.disabled(store.searching)
                }
            }
            .listStyle(.plain)
            .environment(\.defaultMinListRowHeight,0)
            .background { GeometryReader { geo in
                Color.clear.onAppear { inboxFrame=geo.frame(in:.global) }
                    .onChange(of:geo.frame(in:.global)) { _,frame in inboxFrame=frame }
            } }
            .onPreferenceChange(MailRowFrames.self) { rowFrames=$0 }
            .offset(y:reduceMotion ? 0 : 12*(1-filterEntrance))
            .opacity(filterEntrance)

            .onChange(of:showFiltered) { _,_ in
                cleanup.cancel()
                prepareCleanup()
                var transaction = Transaction()
                transaction.disablesAnimations = true
                withTransaction(transaction) { scroll.scrollTo(ScrollAnchor.refreshNotice,anchor:.top) }
                animateFilterEntrance()
            }
            .overlay { if displayed.isEmpty && !store.searching && store.error == nil { Text("No mail").foregroundStyle(.tertiary) } }
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
            .refreshable {
                // Release the pull-to-refresh spinner; the fixed notice shows
                // progress after the list settles back into place.
                Task { await store.refresh(sync:true,announce:true) }
            }
            .navigationDestination(for:String.self) { ReaderView(messageID:$0) }
            }
        }
        .overlay {
            GeometryReader { geo in
                ForEach(cleanup.flights) { flight in
                    Image(systemName:"envelope.fill")
                        .font(.system(size:19,weight:.medium))
                        .foregroundStyle(.white)
                        .frame(width:38,height:30)
                        .background(Color.accentColor,in:RoundedRectangle(cornerRadius:8))
                        .modifier(CleanupFlightMotion(progress:cleanup.progress,flight:flight,
                            origin:geo.frame(in:.global).origin))
                }
            }.allowsHitTesting(false).accessibilityHidden(true)
        }
        .task(id:cleanupKey) { prepareCleanup() }
        .onChange(of:scenePhase) { _,phase in
            if phase == .active { prepareCleanup() } else { cleanup.cancel() }
        }
        .onChange(of:store.path) { _,_ in cleanup.cancel(); prepareCleanup() }
        .onChange(of:store.searchText) { _,_ in cleanup.cancel(); prepareCleanup() }
        .onChange(of:store.submittedSearch) { _,_ in cleanup.cancel(); prepareCleanup() }
        .onChange(of:reduceMotion) { _,_ in cleanup.cancel(); prepareCleanup() }
        .onChange(of:cleanup.completedBatches) { _,_ in prepareCleanup() }
        .onChange(of:filterFrame) { _,_ in prepareCleanup() }
        .onChange(of:inboxFrame) { _,_ in prepareCleanup() }
        .onDisappear {
            cleanup.cancel()
            filterEntranceTask?.cancel()
            filterEntrance=1
        }
        #if DEBUG
        .sheet(isPresented:$showAnimationPreview) { FilterAnimationPreview() }
        #endif
    }
    private var cleanupKey: [String] {
        store.messages.map { "\($0.id):\($0.decision ?? "pending"):\($0.starred == true):\($0.expectedKeep)" }
    }
    private func animateFilterEntrance() {
        filterEntranceTask?.cancel()
        var transaction=Transaction(); transaction.disablesAnimations=true
        withTransaction(transaction) { filterEntrance=0 }
        filterEntranceTask=Task { @MainActor in
            do {
                try await Task.sleep(for:.milliseconds(20))
                try Task.checkCancellation()
                withAnimation(.timingCurve(0.16,1,0.3,1,duration:0.22)) {
                    filterEntrance=1
                }
            } catch { }
        }
    }
    private func prepareCleanup() {
        guard scenePhase == .active, store.path.isEmpty,
              store.searchText.isEmpty, store.submittedSearch.isEmpty else { return }
        cleanup.prepare(messages:store.messages,showFiltered:showFiltered,reduceMotion:reduceMotion,rowHeight:cleanupRowHeight) {
            (rowFrames,filterFrame,inboxFrame)
        }
    }
    private var filteredButton: some View {
        Button {
            UISelectionFeedbackGenerator().selectionChanged()
            showFiltered.toggle()
        } label: {
            Image(systemName:"line.3.horizontal.decrease")
                .foregroundStyle(showFiltered ? Color.blue : Color.primary)
                .scaleEffect(cleanup.buttonPulse ? 0.82 : 1)
                .background { GeometryReader { geo in
                    Color.clear.onAppear { filterFrame=geo.frame(in:.global) }
                        .onChange(of:geo.frame(in:.global)) { _,frame in filterFrame=frame }
                } }
        }
        .accessibilityLabel("Show filtered")
        .accessibilityValue(showFiltered ? "On" : "Off")
        #if DEBUG
        .contextMenu {
            Button("Preview animation",systemImage:"play.circle") {
                cleanup.cancel()
                showAnimationPreview=true
            }
        }
        #endif
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

/// SwiftUI List delegates row sizing to UIKit and may snap height changes.
/// Use a zero-spacing SwiftUI stack so slot heights and subsequent row positions
/// participate in the same layout animation. Keep List on iOS 17 for swipeActions.
private struct InboxRows<Content:View>: View {
    private let content: Content
    init(@ViewBuilder content:()->Content) { self.content=content() }
    var body: some View {
        if #available(iOS 18.0, *) {
            ScrollView {
                LazyVStack(spacing:0) { content }
            }
        } else {
            List { content }.listStyle(.plain).environment(\.defaultMinListRowHeight,0)
        }
    }
}

private struct MailRowFrames: PreferenceKey {
    static var defaultValue: [String:CGRect] = [:]
    static func reduce(value:inout [String:CGRect],nextValue:()->[String:CGRect]) {
        value.merge(nextValue(),uniquingKeysWith: { _,new in new })
    }
}

private struct CleanupFlight: Identifiable {
    let id: String
    let start: CGPoint
    let end: CGPoint
    let delay: Double
}

private enum CleanupTiming {
    static let duration = 1.5
    static let settleDuration = 0.2
    static let shrink = 0.18
    static let flight = 0.6
    static func delay(index:Int,count:Int) -> Double {
        0.2 + 0.4 * (count > 1 ? Double(index) / Double(count - 1) : 1)
    }
    static func unit(_ value:Double) -> Double { min(1,max(0,value)) }
}

/// Keep the divider inside the animated row; List's own separator does not
/// participate in the row's scale/opacity and would linger until removal.
private struct MailRowDivider: ViewModifier {
    @Environment(\.displayScale) private var displayScale
    var showTop = false
    private var line: some View {
        Color(uiColor:.separator).frame(height:1/displayScale)
            .padding(.horizontal,20).allowsHitTesting(false).accessibilityHidden(true)
    }
    func body(content:Content) -> some View {
        content.overlay(alignment:.bottom) {
            line
        }.overlay(alignment:.top) {
            if showTop { line }
        }
    }
}

private struct CleanupRowMotion: AnimatableModifier {
    var progress: Double
    let delay: Double?
    var animatableData: Double {
        get { progress }
        set { progress=newValue }
    }
    func body(content:Content) -> some View {
        let elapsed = progress * CleanupTiming.duration - (delay ?? 0)
        let shrink = delay == nil ? 0 : CleanupTiming.unit(elapsed / CleanupTiming.shrink)
        content
            .scaleEffect(1 - 0.88 * shrink)
            .opacity(1 - shrink)
            .clipped()
            .accessibilityHidden(delay != nil)
    }
}

private struct CleanupFlightMotion: AnimatableModifier {
    var progress: Double
    let flight: CleanupFlight
    let origin: CGPoint
    var animatableData: Double {
        get { progress }
        set { progress=newValue }
    }
    func body(content:Content) -> some View {
        let elapsed = progress * CleanupTiming.duration - flight.delay
        let t = CleanupTiming.unit((elapsed - CleanupTiming.shrink) / CleanupTiming.flight)
        // A bowed path remains on-screen and terminates at the actual toolbar icon.
        let control = CGPoint(x:flight.end.x - 75,y:flight.start.y + (flight.end.y-flight.start.y)*0.25)
        let a=CGFloat((1-t)*(1-t)), b=CGFloat(2*(1-t)*t), c=CGFloat(t*t)
        let x: CGFloat = a*flight.start.x + b*control.x + c*flight.end.x
        let y: CGFloat = a*flight.start.y + b*control.y + c*flight.end.y
        let scale: Double = (0.65 + 0.35 * min(1,t*8)) * (1-0.8*pow(t,4))
        content
            .scaleEffect(scale)
            .rotationEffect(.degrees(sin(t * .pi) * 12))
            .opacity(elapsed >= CleanupTiming.shrink && t < 1 ? 1 : 0)
            .position(x:x-origin.x,y:y-origin.y)
    }
}

@MainActor private final class InboxCleanup: ObservableObject {
    @Published var completedBatches = 0
    @Published var previewIDs = Set<String>()
    @Published var collapsedIDs = Set<String>()
    @Published private var stagedHeights: [String:CGFloat] = [:]
    @Published var delays: [String:Double] = [:]
    @Published var flights: [CleanupFlight] = []
    @Published var progress = 0.0
    @Published var buttonPulse = false
    private let defaults: UserDefaults
    private let remembersPresentation: Bool
    private let seenKey = "mail.cleanup.presented.v1"
    private var seen: Set<String>
    private var job: Task<Void,Never>?
    private var batchIDs = Set<String>()

    init(defaults:UserDefaults = .standard,remembersPresentation:Bool = true) {
        self.defaults=defaults
        self.remembersPresentation=remembersPresentation
        seen=remembersPresentation ? Set(defaults.stringArray(forKey:seenKey) ?? []) : []
    }
    func slotHeight(_ id:String,normal:CGFloat) -> CGFloat {
        collapsedIDs.contains(id) ? 0 : (stagedHeights[id] ?? normal)
    }
    private func stageOffscreenMail(messages:[MailMessage],rowHeight:CGFloat,
                                    frames:[String:CGRect],viewport:CGRect) {
        let displayed=messages.filter {
            previewIDs.contains($0.id) || $0.starred == true || $0.expectedKeep ||
                ["keep","uncertain"].contains($0.decision ?? "")
        }
        guard let next=displayed.firstIndex(where:{ !batchIDs.contains($0.id) }), next > 0,
              let anchor=displayed.indices.first(where:{ (frames[displayed[$0].id]?.height ?? 0) > 0 }),
              let anchorFrame=frames[displayed[anchor].id] else { return }
        let originalTop=frames[displayed[next].id]?.minY ??
            (anchorFrame.minY + CGFloat(next-anchor)*rowHeight)
        let excess=max(0,originalTop-(viewport.maxY+12))
        guard excess > 0 else { return }
        // These slots are already invisible. Compress only the unseen distance,
        // leaving the first surviving message just below the viewport before it moves.
        let shortened=max(0,rowHeight-excess/CGFloat(next))
        var transaction=Transaction(); transaction.disablesAnimations=true
        withTransaction(transaction) {
            stagedHeights=Dictionary(uniqueKeysWithValues:displayed.prefix(next).map { ($0.id,shortened) })
        }
    }
    private func remember(_ ids:Set<String>) {
        guard !ids.isSubset(of:seen) else { return }
        seen.formUnion(ids)
        if remembersPresentation { defaults.set(Array(seen),forKey:seenKey) }
    }
    private func canHide(_ message:MailMessage) -> Bool {
        message.decision == "hide" && message.starred != true && !message.expectedKeep
    }
    func prepare(messages:[MailMessage],showFiltered:Bool,reduceMotion:Bool,rowHeight:CGFloat,
                 geometry:@escaping ()->([String:CGRect],CGRect,CGRect)) {
        // Never animate a message that has since been starred or manually retained.
        if !batchIDs.isSubset(of:Set(messages.filter(canHide).map(\.id))) { cancel() }
        guard job == nil else { return }
        guard !messages.isEmpty else { return }
        // On first enablement, establish a baseline rather than replaying the entire cache.
        if remembersPresentation && defaults.object(forKey:seenKey) == nil {
            remember(Set(messages.filter { $0.decision != nil }.map(\.id)))
            if defaults.object(forKey:seenKey) == nil { defaults.set([String](),forKey:seenKey) }
            return
        }
        if showFiltered || reduceMotion {
            remember(Set(messages.filter { $0.decision != nil }.map(\.id)))
            previewIDs=[]
            return
        }
        let fresh=messages.filter { !seen.contains($0.id) }
        remember(Set(fresh.filter { $0.decision != nil && !canHide($0) }.map(\.id)))
        // Pending mail can briefly appear; it stays until a verdict is available.
        previewIDs=Set(fresh.filter { $0.decision == nil || canHide($0) }.map(\.id))
        let hidden=fresh.filter(canHide)
        guard !hidden.isEmpty else { return }
        self.batchIDs=Set(hidden.map(\.id))
        job=Task { [weak self] in
            do {
                // Allow newly inserted rows to lay out before recording their origins.
                try await Task.sleep(for:.milliseconds(120))
                guard let self else { return }
                let (frames,target,viewport)=geometry()
                guard target.width > 0,viewport.height > 0 else {
                    self.batchIDs=[]; self.job=nil; return
                }
                self.remember(self.batchIDs)
                self.progress=0
                self.flights=hidden.enumerated().map { index,message in
                    let frame=frames[message.id] ?? .zero
                    let visible=frame.intersection(viewport)
                    // Offscreen mail joins the stream at the inbox edge, without auto-scrolling.
                    let start=visible.isNull || visible.height < 20
                        ? CGPoint(x:viewport.midX,y:viewport.maxY-20)
                        : CGPoint(x:frame.midX,y:visible.midY)
                    return CleanupFlight(id:message.id,start:start,
                        end:CGPoint(x:target.midX,y:target.midY),
                        delay:CleanupTiming.delay(index:index,count:hidden.count))
                }
                self.delays=Dictionary(uniqueKeysWithValues:self.flights.map { ($0.id,$0.delay) })
                // Commit the inserted icons at progress zero before starting their animation.
                try await Task.sleep(for:.milliseconds(20))
                try Task.checkCancellation()
                let clock=ContinuousClock(); let start=clock.now
                let haptic=UIImpactFeedbackGenerator(style:.light)
                haptic.prepare()
                withAnimation(.linear(duration:CleanupTiming.duration)) { self.progress=1 }
                var lastImpact = -Double.infinity
                for (index,flight) in self.flights.enumerated() {
                    let arrival=flight.delay+CleanupTiming.shrink+CleanupTiming.flight
                    let final=index == hidden.count-1
                    guard arrival-lastImpact >= 0.12 || final else { continue }
                    try await clock.sleep(until:start.advanced(by:.seconds(arrival)))
                    try Task.checkCancellation()
                    haptic.impactOccurred(intensity:final ? 0.85 : 0.5)
                    lastImpact=arrival
                    withAnimation(.easeOut(duration:0.045)) { self.buttonPulse=true }
                    try await Task.sleep(for:.milliseconds(45))
                    withAnimation(.spring(response:0.18,dampingFraction:0.65)) { self.buttonPulse=false }
                }
                try await clock.sleep(until:start.advanced(by:.seconds(CleanupTiming.duration)))
                try Task.checkCancellation()
                // Collection finishes first; only then close the empty row slots.
                self.flights=[]
                let (currentFrames,_,currentViewport)=geometry()
                self.stageOffscreenMail(messages:messages,rowHeight:rowHeight,frames:currentFrames,viewport:currentViewport)
                // Lay out the offscreen starting position before interpolating upward.
                try await Task.sleep(for:.milliseconds(20))
                try Task.checkCancellation()
                let settleDuration=CleanupTiming.settleDuration
                let settleStart=clock.now
                withAnimation(.timingCurve(0.16,1,0.3,1,duration:settleDuration)) {
                    self.collapsedIDs=self.batchIDs
                }
                try await clock.sleep(until:settleStart.advanced(by:.seconds(settleDuration)))
                try Task.checkCancellation()
                // Rows are already zero-height, so removal cannot shift the remaining mail.
                self.previewIDs.subtract(self.batchIDs)
                self.flights=[];self.delays=[:];self.collapsedIDs=[];self.stagedHeights=[:];self.batchIDs=[];self.job=nil
                self.completedBatches += 1
            } catch { /* cancellation is finalized synchronously by cancel() */ }
        }
    }
    func cancel() {
        job?.cancel();job=nil
        previewIDs=[];flights=[];delays=[:];collapsedIDs=[];stagedHeights=[:];batchIDs=[]
        var transaction=Transaction();transaction.disablesAnimations=true
        withTransaction(transaction) { progress=0;buttonPulse=false }
    }
}

#if DEBUG
/// Uses the real animation engine with disposable, in-memory mail and no MailStore/API.
private struct FilterAnimationPreview: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.scenePhase) private var phase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @StateObject private var cleanup = InboxCleanup(remembersPresentation:false)
    @State private var count = 5
    @State private var messages: [MailMessage] = []
    @State private var frames: [String:CGRect] = [:]
    @State private var target = CGRect.zero
    @State private var viewport = CGRect.zero
    @ScaledMetric(relativeTo:.body) private var rowHeight: CGFloat = 108
    private var visible: [MailMessage] {
        messages.filter { $0.decision != "hide" || cleanup.previewIDs.contains($0.id) }
    }
    var body: some View {
        NavigationStack {
            VStack(spacing:0) {
                Picker("New mail",selection:$count) {
                    Text("1 mail").tag(1)
                    Text("5 mails").tag(5)
                    Text("20 mails").tag(20)
                }.pickerStyle(.segmented).padding(.horizontal).padding(.bottom,12)
                InboxRows {
                    ForEach(visible) { message in
                    MailRow(message:message)
                        .padding(.horizontal,20).frame(height:rowHeight)
                        .background(message.decision == "hide"
                            ? Color.red.opacity(colorScheme == .dark ? 0.22 : 0.14) : .clear)
                        .modifier(MailRowDivider(showTop:message.id == visible.first?.id))
                        .background { GeometryReader { geo in
                            Color.clear.preference(key:MailRowFrames.self,value:[message.id:geo.frame(in:.global)])
                        } }
                        .modifier(CleanupRowMotion(progress:cleanup.progress,delay:cleanup.delays[message.id]))
                        .frame(height:cleanup.slotHeight(message.id,normal:rowHeight))
                        .clipped()
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                }
                }
                .onPreferenceChange(MailRowFrames.self) { frames=$0 }
                .background { GeometryReader { geo in
                    Color.clear.onAppear { viewport=geo.frame(in:.global) }
                        .onChange(of:geo.frame(in:.global)) { _,frame in viewport=frame }
                } }
            }
            .navigationTitle("Animation preview").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement:.topBarLeading) { Button("Done") { dismiss() } }
                ToolbarItem(placement:.topBarTrailing) {
                    Button("Replay",systemImage:"arrow.clockwise") { replay() }
                }
                ToolbarItemGroup(placement:.bottomBar) {
                    Spacer()
                    Button { replay() } label: {
                        Image(systemName:"line.3.horizontal.decrease")
                            .scaleEffect(cleanup.buttonPulse ? 0.82 : 1)
                            .background { GeometryReader { geo in
                                Color.clear.onAppear { target=geo.frame(in:.global) }
                                    .onChange(of:geo.frame(in:.global)) { _,frame in target=frame }
                            } }
                    }.accessibilityLabel("Filter — replay animation")
                }
            }
        }
        .overlay {
            GeometryReader { geo in
                ForEach(cleanup.flights) { flight in
                    Image(systemName:"envelope.fill").font(.system(size:19,weight:.medium))
                        .foregroundStyle(.white).frame(width:38,height:30)
                        .background(Color.accentColor,in:RoundedRectangle(cornerRadius:8))
                        .modifier(CleanupFlightMotion(progress:cleanup.progress,flight:flight,origin:geo.frame(in:.global).origin))
                }
            }.allowsHitTesting(false).accessibilityHidden(true)
        }
        .task { replay() }
        .onChange(of:count) { _,_ in replay() }
        .onChange(of:target) { _,_ in prepare() }
        .onChange(of:viewport) { _,_ in prepare() }
        .onChange(of:phase) { _,phase in if phase != .active { cleanup.cancel() } }
        .onDisappear { cleanup.cancel() }
    }
    private func replay() {
        cleanup.cancel()
        let batch=UUID().uuidString
        let hidden=(0..<count).map { index in
            sample(id:"\(batch)-\(index)",sender:["Daily Digest","Offers","Updates"][index % 3],
                   subject:["Your weekly roundup","A little something for you","What's new this week"][index % 3],decision:"hide")
        }.shuffled()
        let kept=[
            sample(id:"\(batch)-keep-1",sender:"Alex",subject:"See you tomorrow",decision:"keep"),
            sample(id:"\(batch)-keep-2",sender:"Sam",subject:"Your registration is confirmed",decision:"keep"),
            sample(id:"\(batch)-keep-3",sender:"Jordan",subject:"Re: A quick question",decision:"keep")
        ].shuffled()
        // Guarantee both kinds are visible at the top, then mix the rest anew.
        messages=[hidden[0],kept[0]].shuffled()
            + (Array(hidden.dropFirst())+Array(kept.dropFirst())).shuffled()
        prepare()
    }
    private func prepare() {
        cleanup.prepare(messages:messages,showFiltered:false,reduceMotion:reduceMotion,rowHeight:rowHeight) { (frames,target,viewport) }
    }
    private func sample(id:String,sender:String,subject:String,decision:String) -> MailMessage {
        MailMessage(id:id,sender:sender,email:"preview@example.com",mailbox:"preview@example.com",
                    subject:subject,date:Date().timeIntervalSince1970,received:Date().timeIntervalSince1970,
                    color:"#58aa88",unread:true,starred:false,expectedKeep:false,decision:decision)
    }
}
#endif


/// Text stays stationary; the clipping window reveals and then erases left to right.
private struct RefreshNotice: View {
    @Binding var feedback: RefreshFeedback
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var reveal = 0.0
    @State private var erase = 0.0
    var body: some View {
        Group {
            if feedback != .idle {
                Text(feedback == .checking ? "Checking…" : "No new mail")
                    .font(.callout).foregroundStyle(.secondary)
                    .mask {
                        GeometryReader { geo in
                            Rectangle()
                                .frame(width:geo.size.width * max(0,reveal-erase))
                                .offset(x:geo.size.width * erase)
                        }
                    }
                    .frame(maxWidth:.infinity,alignment:.leading)
                    .padding(.horizontal,20)
                    .allowsHitTesting(false)
                    .transition(.opacity)
                    .accessibilityLabel(feedback == .checking ? "Checking for new mail" : "No new mail")
            }
        }
        .animation(reduceMotion ? nil : .easeOut(duration:0.18),value:feedback == .idle)
        .task(id:feedback) {
            var transaction=Transaction(); transaction.disablesAnimations=true
            withTransaction(transaction) { reveal=feedback == .checking ? 1 : 0; erase=0 }
            guard feedback == .noNewMail else { return }
            do {
                try await Task.sleep(for:.milliseconds(20))
                withAnimation(reduceMotion ? nil : .easeInOut(duration:0.28)) { reveal=1 }
                try await Task.sleep(for:.milliseconds(reduceMotion ? 1000 : 1280))
                try Task.checkCancellation()
                withAnimation(reduceMotion ? nil : .easeInOut(duration:0.28)) { erase=1 }
                try await Task.sleep(for:.milliseconds(reduceMotion ? 0 : 280))
                try Task.checkCancellation()
                feedback = .idle
            } catch { }
        }
    }
}
