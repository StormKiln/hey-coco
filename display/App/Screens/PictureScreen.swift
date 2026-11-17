import CocoKit
import SwiftUI

/// `ViewSpec.picture` (#33): the image full screen with its title and attribution. Swipe for the
/// next or previous result; the Hub's "next one" sends a new picture view (images rotated), which
/// starts again at its first image. The Close (X) button or a minute without a swipe goes back
/// to idle. The photo stays out from under the top chrome (wake-word hint, gear, Close): below
/// the Close button in portrait, below the top row and in from the Close column in landscape,
/// and above the mic button.
struct PictureScreen: View {
    let payload: ViewSpec.PicturePayload
    @Environment(AppModel.self) private var model
    @State private var carousel = PictureCarousel()

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let image = carousel.current {
                GeometryReader { geometry in
                    let landscape = geometry.size.width > geometry.size.height
                    RemoteImage(reference: image.displayURL)
                        .accessibilityLabel(image.title)
                        .padding(.top, landscape ? ScreenChrome.top : ScreenChrome.belowClose)
                        .padding(.horizontal, landscape ? ScreenChrome.closeColumn : 0)
                        // Above the mic button; the caption sits over the photo's foot.
                        .padding(.bottom, ScreenChrome.bottom)
                        .frame(width: geometry.size.width, height: geometry.size.height)
                }
                .ignoresSafeArea()
                .id(image.displayURL)
                caption(for: image)
            } else {
                Text("No pictures of \(payload.query).")
                    .m3Font(.headlineSmall)
                    .foregroundStyle(.white.opacity(0.8))
            }
        }
        .contentShape(Rectangle())
        .gesture(
            DragGesture(minimumDistance: 40).onEnded { drag in
                guard abs(drag.translation.width) > abs(drag.translation.height) else { return }
                withAnimation { drag.translation.width < 0 ? carousel.next() : carousel.previous() }
            }
        )
        .task(id: payload) {
            carousel.onIdle = { [model] in
                if case .picture = model.currentView { model.show(.idle) }
            }
            carousel.show(payload.images)
        }
        .onDisappear { carousel.stop() }
        .closeButton()
    }

    private func caption(for image: PictureImage) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Spacer()
            Text(PictureCaption.tidy(image.title, author: image.author, fallback: payload.query))
                .m3Font(.titleMedium)
                .lineLimit(2)
            HStack {
                Text(image.attributionLine)
                    .m3Font(.labelSmall, weight: .regular)
                    .opacity(0.75)
                    .lineLimit(1)
                Spacer()
                if carousel.canAdvance {
                    Text("\(carousel.index + 1) of \(carousel.images.count)")
                        .m3Font(.labelMedium)
                        .monospacedDigit()
                        .padding(.horizontal, 16)
                        .frame(minHeight: 40)
                        // A badge-like chip on the photo.
                        .m3Surface(.black.opacity(0.45), shape: nil)
                }
            }
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 32)
        // Clear of the mic button.
        .padding(.bottom, 150)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(alignment: .bottom) {
            LinearGradient(colors: [.clear, .black.opacity(0.7)], startPoint: .top, endPoint: .bottom)
                .frame(height: 320)
                .ignoresSafeArea()
                .allowsHitTesting(false)
        }
    }
}
