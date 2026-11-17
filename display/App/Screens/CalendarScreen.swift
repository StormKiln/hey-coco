import CocoKit
import SwiftUI

/// `ViewSpec.calendar` (#143): the answer to "what's on Saturday?" or "when is soccer?". The
/// title, then a card per day: its date, all-day rows first, then timed rows (calendar colour,
/// time, title, calendar), "+N more" past `CalendarAnswer.dayLimit`. Several days scroll. What
/// shows is decided in CocoKit (`CalendarAnswer`), in the Hub's timezone; this only draws it.
/// Close (X) top right, and back to idle after `CalendarAnswer.idleTimeout` without a touch (not
/// while Coco is listening or talking). Laid out for the kitchen iPad (810x1080pt, 7th gen).
struct CalendarScreen: View {
    let payload: ViewSpec.CalendarPayload
    let idleInfo: IdleInfo?
    @Environment(AppModel.self) private var model
    @Environment(VoiceLoop.self) private var voice: VoiceLoop?
    @State private var autoReturn: AutoReturnTimer?

    /// The Hub's timezone, as on the home screen.
    private var timeZone: TimeZone {
        idleInfo.flatMap { TimeZone(identifier: $0.timezone) } ?? model.night.timeZone
    }
    private var busy: Bool { voice?.conversation.state.isActive ?? false }

    var body: some View {
        GeometryReader { geometry in
            let landscape = geometry.size.width > geometry.size.height
            TimelineView(.everyMinute) { context in
                let content = CalendarAnswer.content(payload, timeZone: timeZone, now: context.date)
                VStack(alignment: .leading, spacing: 16) {
                    header(content, landscape: landscape)
                    list(content)
                }
                // Landscape: a centred column clear of the Close button's column. Portrait: the
                // full width, the list starting below the Close button.
                .frame(maxWidth: landscape ? 800 : .infinity)
                .padding(.horizontal, landscape ? ScreenChrome.closeColumn : 48)
                .frame(maxWidth: .infinity)
            }
        }
        .padding(.top, ScreenChrome.top)
        .contentShape(Rectangle())
        .simultaneousGesture(TapGesture().onEnded { autoReturn?.noteInteraction() })
        .closeButton()
        .onAppear {
            autoReturn = AutoReturnTimer(timeout: CalendarAnswer.idleTimeout, views: [.calendar]) {
                [model] in model.show(.idle)
            }
            updateAutoReturn()
        }
        .onDisappear { autoReturn = nil }
        .onChange(of: model.currentView) { updateAutoReturn() }
        .onChange(of: busy) { updateAutoReturn() }
    }

    private func updateAutoReturn() {
        autoReturn?.update(view: model.currentView, busy: busy)
    }

    /// The title, and the stale chip when the Hub's calendars are out of date.
    private func header(_ content: CalendarAnswerContent, landscape: Bool) -> some View {
        HStack(alignment: .center, spacing: 16) {
            Text(content.title)
                .m3Font(.headlineSmall)
                .foregroundStyle(Theme.onSurface)
                .lineLimit(2)
                .minimumScaleFactor(0.6)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 0)
            if let stale = content.staleLabel {
                StaleChip(text: stale)
            }
        }
        // Portrait: keep left of the Close button, and tall enough that the list starts below it.
        .padding(.trailing, landscape ? 0 : ScreenChrome.closeColumn - 48)
        .frame(minHeight: landscape ? nil : ScreenChrome.belowClose - ScreenChrome.top - 16, alignment: .bottom)
    }

    private func list(_ content: CalendarAnswerContent) -> some View {
        ScrollView {
            VStack(spacing: 16) {
                if let empty = content.emptyText {
                    emptyCard(empty)
                }
                ForEach(content.days) { day in
                    DayCard(day: day)
                }
            }
            .padding(.bottom, ScreenChrome.bottom)
        }
        .scrollBounceBehavior(.basedOnSize)
        .scrollIndicators(.hidden)
        .onScrollPhaseChange { _, phase in
            if phase != .idle { autoReturn?.noteInteraction() }
        }
    }

    private func emptyCard(_ text: String) -> some View {
        Label(text, systemImage: "calendar")
            .m3Font(.titleSmall)
            .labelStyle(MaterialLabelStyle())
            .foregroundStyle(Theme.onSurfaceVariant)
            .padding(.horizontal, 28)
            .padding(.vertical, 32)
            .frame(maxWidth: .infinity)
            .m3Surface(Theme.surfaceContainerLow, shape: .extraLarge)
    }
}

/// One day: "Saturday, November 28", its rows, "+N more" or "Nothing on the calendar".
private struct DayCard: View {
    let day: CalendarAnswerDay

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(day.header)
                .m3Font(.titleSmall)
                .foregroundStyle(Theme.primary)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .accessibilityAddTraits(.isHeader)
                .padding(.bottom, 2)
            if let empty = day.emptyText {
                Text(empty)
                    .m3Font(.bodyMedium)
                    .foregroundStyle(Theme.onSurfaceVariant)
            }
            ForEach(day.rows) { row in
                CalendarRowView(row: row, showsCalendar: true)
            }
            if let more = day.moreText {
                Text(more)
                    .m3Font(.labelMedium, weight: .medium)
                    .foregroundStyle(Theme.onSurfaceVariant)
                    .padding(.leading, 26)
                    .accessibilityLabel(more.replacingOccurrences(of: "+", with: "") + " events")
            }
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .m3Surface(Theme.surfaceContainerLow, shape: .extraLarge)
    }
}
