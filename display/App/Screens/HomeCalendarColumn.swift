import CocoKit
import SwiftUI

/// The home screen's calendar column (#142): what's left today and, on a light day, tomorrow.
/// Which rows show is decided in CocoKit (`HomeCalendar`); this only draws them.
struct HomeCalendarColumn: View {
    let content: HomeCalendarContent

    var body: some View {
        CalendarSectionsLayout {
            CalendarSectionView(section: content.today, staleLabel: content.staleLabel)
            if let tomorrow = content.tomorrow {
                CalendarSectionView(section: tomorrow, staleLabel: nil)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct CalendarSectionView: View {
    let section: HomeCalendarSection
    let staleLabel: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 16) {
                Text(section.title)
                    .m3Font(.titleSmall)
                    .foregroundStyle(Theme.primary)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize()
                if let staleLabel {
                    Spacer(minLength: 0)
                    StaleChip(text: staleLabel)
                }
            }
            if let empty = section.emptyText {
                Text(empty)
                    .m3Font(.bodyMedium)
                    .foregroundStyle(Theme.onSurfaceVariant)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            ForEach(section.rows) { row in
                CalendarRowView(row: row)
            }
            if let more = section.moreText {
                Text(more)
                    .m3Font(.labelMedium, weight: .medium)
                    .foregroundStyle(Theme.onSurfaceVariant)
                    .padding(.leading, 26)
                    .accessibilityLabel(more.replacingOccurrences(of: "+", with: "") + " events")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// "Updated 2:15 PM" beside "Today" when the Hub's calendars are out of date.
private struct StaleChip: View {
    let text: String

    var body: some View {
        Label(text, systemImage: "clock.badge.exclamationmark")
            .m3Font(.labelMedium)
            .labelStyle(MaterialLabelStyle())
            .foregroundStyle(Theme.onTertiaryContainer)
            .lineLimit(1)
            .minimumScaleFactor(0.7)
            .padding(.horizontal, 16)
            .frame(minHeight: 40)
            .m3Surface(Theme.tertiaryContainer, shape: .medium)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Calendar \(text.prefix(1).lowercased() + text.dropFirst())")
    }
}

/// Today and Tomorrow side by side when there is room (the full-width card in portrait), else
/// one under the other (the calendar column in landscape).
private struct CalendarSectionsLayout: Layout {
    var minWidthForColumns: CGFloat = 560
    var columnSpacing: CGFloat = 32
    var rowSpacing: CGFloat = 16

    private func columnWidth(_ width: CGFloat, _ count: Int) -> CGFloat? {
        guard count == 2, width >= minWidthForColumns else { return nil }
        return ((width - columnSpacing) / 2).rounded(.down)
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 420
        if let column = columnWidth(width, subviews.count) {
            let height = subviews.map { $0.sizeThatFits(.init(width: column, height: nil)).height }.max() ?? 0
            return CGSize(width: width, height: height)
        }
        let heights = subviews.map { $0.sizeThatFits(.init(width: width, height: nil)).height }
        return CGSize(width: width, height: heights.reduce(0, +) + rowSpacing * CGFloat(max(0, heights.count - 1)))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        if let column = columnWidth(bounds.width, subviews.count) {
            for (index, subview) in subviews.enumerated() {
                let x = bounds.minX + CGFloat(index) * (column + columnSpacing)
                subview.place(at: CGPoint(x: x, y: bounds.minY), proposal: .init(width: column, height: nil))
            }
            return
        }
        var y = bounds.minY
        for subview in subviews {
            let height = subview.sizeThatFits(.init(width: bounds.width, height: nil)).height
            subview.place(at: CGPoint(x: bounds.minX, y: y), proposal: .init(width: bounds.width, height: height))
            y += height + rowSpacing
        }
    }
}

private struct CalendarRowView: View {
    let row: HomeCalendarRow

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Circle()
                .fill(row.color.map { Color(RGBColor(hex: $0)) } ?? Theme.primary)
                .frame(width: 14, height: 14)
                .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 1 }
            Text(row.time)
                .m3Font(.labelLarge, weight: .medium)
                .monospacedDigit()
                .foregroundStyle(Theme.onSurfaceVariant)
                .lineLimit(1)
                .fixedSize()
            Text(row.title)
                .m3Font(.bodyMedium)
                .foregroundStyle(Theme.onSurface)
                .lineLimit(1)
                .truncationMode(.tail)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(row.accessibilityLabel)
    }
}

/// Forecast, divider, calendar. Side by side when the forecast column gets at least
/// `minForecastWidth` (landscape): the forecast takes about 45% of the width, a little more (up to
/// half) if its current conditions need it, so it keeps the sizes it has without a calendar.
/// Otherwise stacked, the forecast full width as on a home screen without calendars (portrait).
struct ForecastCalendarLayout: Layout {
    var forecastFraction: CGFloat = 0.45
    var maxForecastFraction: CGFloat = 0.5
    var minForecastWidth: CGFloat = 360
    var spacing: CGFloat = 28

    /// Forecast and calendar widths side by side, or nil to stack.
    private func columns(_ width: CGFloat, _ subviews: Subviews) -> (forecast: CGFloat, calendar: CGFloat)? {
        let available = width - 2 * spacing - 1
        let ideal = subviews[0].sizeThatFits(.unspecified).width
        let forecast = min(max(available * forecastFraction, ideal), available * maxForecastFraction).rounded()
        guard forecast >= minForecastWidth else { return nil }
        return (forecast, available - forecast)
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard subviews.count == 3 else { return .zero }
        let width = proposal.width ?? 896
        if let (forecast, calendar) = columns(width, subviews) {
            let height = max(
                subviews[0].sizeThatFits(.init(width: forecast, height: nil)).height,
                subviews[2].sizeThatFits(.init(width: calendar, height: nil)).height)
            return CGSize(width: width, height: height)
        }
        let height = subviews[0].sizeThatFits(.init(width: width, height: nil)).height + 2 * spacing + 1
            + subviews[2].sizeThatFits(.init(width: width, height: nil)).height
        return CGSize(width: width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard subviews.count == 3 else { return }
        if let (forecast, calendar) = columns(bounds.width, subviews) {
            var x = bounds.minX
            subviews[0].place(
                at: CGPoint(x: x, y: bounds.minY), proposal: .init(width: forecast, height: bounds.height))
            x += forecast + spacing
            subviews[1].place(at: CGPoint(x: x, y: bounds.minY), proposal: .init(width: 1, height: bounds.height))
            x += 1 + spacing
            subviews[2].place(
                at: CGPoint(x: x, y: bounds.minY), proposal: .init(width: calendar, height: bounds.height))
            return
        }
        var y = bounds.minY
        let forecastHeight = subviews[0].sizeThatFits(.init(width: bounds.width, height: nil)).height
        subviews[0].place(
            at: CGPoint(x: bounds.minX, y: y), proposal: .init(width: bounds.width, height: forecastHeight))
        y += forecastHeight + spacing
        subviews[1].place(at: CGPoint(x: bounds.minX, y: y), proposal: .init(width: bounds.width, height: 1))
        y += 1 + spacing
        subviews[2].place(at: CGPoint(x: bounds.minX, y: y), proposal: .init(width: bounds.width, height: nil))
    }
}
