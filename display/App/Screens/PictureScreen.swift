import CocoKit
import SwiftUI

/// `ViewSpec.picture` (#33): the image full screen with its title and attribution. Swipe for the
/// next or previous result; the Hub's "next one" sends a new picture view (images rotated), which
/// starts again at its first image. Back to idle after a minute without a swipe.
struct PictureScreen: View {
    let payload: ViewSpec.PicturePayload
    @Environment(AppModel.self) private var model
    @State private var carousel = PictureCarousel()

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let image = carousel.current {
                RemoteImage(reference: image.displayURL)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .ignoresSafeArea()
                    .id(image.displayURL)
                    .accessibilityLabel(image.title)
                caption(for: image)
            } else {
                Text("No pictures of \(payload.query).")
                    .font(.system(size: 44, weight: .semibold, design: .rounded))
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
    }

    private func caption(for image: PictureImage) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Spacer()
            Text(PictureCaption.tidy(image.title, author: image.author, fallback: payload.query))
                .font(.system(size: 34, weight: .semibold, design: .rounded))
                .lineLimit(2)
            HStack {
                Text(image.attributionLine)
                    .font(.footnote)
                    .opacity(0.75)
                    .lineLimit(1)
                Spacer()
                if carousel.canAdvance {
                    Text("\(carousel.index + 1) of \(carousel.images.count)")
                        .font(.footnote.monospacedDigit())
                        .opacity(0.75)
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
