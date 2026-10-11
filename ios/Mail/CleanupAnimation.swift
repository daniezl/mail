import SwiftUI

enum CleanupStyle: String, CaseIterable, Identifiable {
    case swipe, shrink
    var id: String { rawValue }
    var title: String { self == .swipe ? "Swipe away" : "Shrink" }
    var duration: Double { self == .swipe ? 1.25 : 0.95 }
}

/// A batch always takes the same time: more mail just launches closer together.
enum CleanupTiming {
    /// Let the inbox show newly arrived mail before cleaning it up.
    static let settle = 0.65
    /// Time between the first and the last launch of a batch.
    static let window = 0.8
}

struct CleanupFlight: Identifiable {
    let id = UUID()
    let message: MailMessage
    /// The row's on-screen frame, or nil when the row was scrolled out of view.
    let start: CGRect?
    let target: CGPoint
    let bounds: CGSize
    let style: CleanupStyle
}

/// Plain reference storage, so frame updates while scrolling never re-render the inbox.
@MainActor final class CleanupCoordinator {
    var rows: [String: CGRect] = [:]
    var button: CGRect = .zero
    var overlay: CGRect = .zero
    private let impact = UIImpactFeedbackGenerator(style:.rigid)
    private var lastImpact = Date.distantPast

    func prepare() { impact.prepare() }
    func land() {
        let now = Date()
        // Taps closer together than this blur into one buzz on the Taptic Engine.
        guard now.timeIntervalSince(lastImpact) > 0.05 else { return }
        lastImpact = now
        impact.impactOccurred()
        impact.prepare()
    }
    func start(for id: String) -> CGRect? {
        guard let frame = rows[id] else { return nil }
        let local = frame.offsetBy(dx:-overlay.minX,dy:-overlay.minY)
        let visible = CGRect(origin:.zero,size:overlay.size).insetBy(dx:0,dy:80)
        return visible.intersects(local) ? local : nil
    }
    func target() -> CGPoint {
        let size = overlay.size
        let center = CGPoint(x:button.midX - overlay.minX,y:button.midY - overlay.minY)
        // Toolbar items are hosted outside the inbox; fall back to the bottom
        // bar's trailing slot if their reported frame is not usable.
        if button.width > 0, center.x > 0, center.x < size.width, center.y > size.height / 2, center.y < size.height { return center }
        return CGPoint(x:size.width - 48,y:size.height - 62)
    }
}

struct CleanupFlightView: View {
    let flight: CleanupFlight
    let onLand: () -> Void
    @State private var progress = 0.0
    var body: some View {
        CleanupFlightFrame(flight:flight,progress:progress)
            .onAppear {
                withAnimation(.linear(duration:flight.style.duration)) { progress = 1 } completion: { onLand() }
            }
    }
}

/// The whole path is a function of one linear progress value, so each style
/// can shape its own phases (and easing) independently.
private struct CleanupFlightFrame: View, Animatable {
    let flight: CleanupFlight
    var progress: Double
    var animatableData: Double {
        get { progress }
        set { progress = newValue }
    }
    var body: some View {
        let pose = flight.pose(at:progress)
        let rowSize = flight.start?.size ?? CGSize(width:flight.bounds.width,height:108)
        ZStack {
            Color(uiColor:.systemBackground).opacity(1 - pose.icon)
            Color(uiColor:.label).opacity(pose.icon)
            MailRow(message:flight.message)
                .padding(.horizontal,20).padding(.vertical,10)
                .frame(width:rowSize.width,height:rowSize.height)
                .opacity(pose.row)
            Image(systemName:"envelope.fill")
                .font(.system(size:20,weight:.semibold))
                .foregroundStyle(Color(uiColor:.systemBackground))
                .opacity(pose.icon)
        }
        .frame(width:pose.size.width,height:pose.size.height)
        .clipShape(RoundedRectangle(cornerRadius:pose.corner,style:.continuous))
        .shadow(color:.black.opacity(0.18 * pose.lift),radius:16 * pose.lift,y:6 * pose.lift)
        .scaleEffect(pose.scale)
        .rotationEffect(.degrees(pose.rotation))
        .opacity(pose.opacity)
        .position(pose.center)
    }
}

struct CleanupPose {
    var center: CGPoint
    var size: CGSize
    var corner: CGFloat = 0
    var row: Double = 0
    var icon: Double = 0
    var opacity: Double = 1
    var scale: CGFloat = 1
    var rotation: Double = 0
    var lift: Double = 0
}

private let iconSize = CGSize(width:52,height:52)
private let landedScale: CGFloat = 0.42

extension CleanupFlight {
    func pose(at p: Double) -> CleanupPose {
        switch style {
        case .swipe: return swipePose(p)
        case .shrink: return shrinkPose(p)
        }
    }

    /// Slide left off screen, loop around out of sight, come back from the
    /// left edge as an envelope and arc into the filter button.
    private func swipePose(_ p: Double) -> CleanupPose {
        let rowY = start?.midY ?? bounds.height * 0.3
        if p < 0.3 {
            guard let start else { return CleanupPose(center:.zero,size:iconSize,opacity:0) }
            let s = segment(p,0,0.3), e = s * s * s
            return CleanupPose(center:CGPoint(x:start.midX - (start.maxX + 30) * e,y:start.midY),
                               size:start.size,corner:mix(0,14,min(1,s * 3)),row:1,
                               rotation:-5 * e,lift:min(1,s * 3))
        }
        let entry = CGPoint(x:-iconSize.width,y:mix(rowY,target.y,0.45))
        if p < 0.5 { return CleanupPose(center:entry,size:iconSize,opacity:0) }
        let t = easeInOut(segment(p,0.5,1))
        let control = CGPoint(x:bounds.width * 0.5,y:entry.y - 140)
        return CleanupPose(center:curve(entry,control,target,t),size:iconSize,corner:iconSize.width / 2,icon:1,
                           opacity:1 - segment(p,0.97,1),scale:mix(1,landedScale,t * t),
                           rotation:-24 * (1 - t),lift:1 - t)
    }

    /// Collapse in place into an envelope at the middle of the row, then get
    /// pulled along an arc into the filter button.
    private func shrinkPose(_ p: Double) -> CleanupPose {
        let rowY = start?.midY ?? bounds.height * 0.35
        let middle = CGPoint(x:bounds.width / 2,y:rowY)
        if p < 0.5 {
            let e = easeInOut(segment(p,0,0.4))
            guard let start else {
                return CleanupPose(center:middle,size:iconSize,corner:iconSize.width / 2,icon:1,
                                   scale:mix(0.2,1,easeOut(segment(p,0,0.4))),lift:e)
            }
            return CleanupPose(center:mix(CGPoint(x:start.midX,y:start.midY),middle,e),
                               size:CGSize(width:mix(start.width,iconSize.width,e),height:mix(start.height,iconSize.height,e)),
                               corner:mix(0,iconSize.width / 2,e),row:1 - min(1,e * 1.8),
                               icon:segment(e,0.45,1),lift:e)
        }
        let t = segment(p,0.5,1)
        let control = CGPoint(x:middle.x + (target.x - middle.x) * 1.05,y:middle.y + (target.y - middle.y) * 0.12)
        return CleanupPose(center:curve(middle,control,target,t * t),size:iconSize,corner:iconSize.width / 2,icon:1,
                           opacity:1 - segment(p,0.97,1),scale:mix(1,landedScale,t),lift:1 - t)
    }
}

private func segment(_ p: Double, _ from: Double, _ to: Double) -> Double { min(1,max(0,(p - from) / (to - from))) }
private func easeInOut(_ t: Double) -> Double { t < 0.5 ? 4 * t * t * t : 1 - pow(-2 * t + 2,3) / 2 }
private func easeOut(_ t: Double) -> Double { 1 - pow(1 - t,3) }
private func mix(_ a: CGFloat, _ b: CGFloat, _ t: Double) -> CGFloat { a + (b - a) * CGFloat(t) }
private func mix(_ a: CGPoint, _ b: CGPoint, _ t: Double) -> CGPoint { CGPoint(x:mix(a.x,b.x,t),y:mix(a.y,b.y,t)) }
private func curve(_ from: CGPoint, _ control: CGPoint, _ to: CGPoint, _ t: Double) -> CGPoint {
    mix(mix(from,control,t),mix(control,to,t),t)
}
