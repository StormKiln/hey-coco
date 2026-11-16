import CocoKit
import SwiftUI

/// Settings' "Home location" row (#96): the place Coco gives home weather for. Tapping it opens
/// `HomeLocationView`. The logic is `HomeLocationModel` in CocoKit.
struct HomeLocationRow: View {
    @Environment(AppModel.self) private var app
    @State private var location = HomeLocationModel()

    var body: some View {
        NavigationLink {
            HomeLocationView(location: location)
        } label: {
            LabeledContent("Home location") {
                Text(location.summary).lineLimit(1)
            }
        }
        .task { await location.load(using: app.hubClient) }
    }
}

/// Type a place, tap Find: the Hub looks it up and saves the best match. Other matches are
/// listed to pick instead; "Use the Hub's default" goes back to the Hub's config file.
struct HomeLocationView: View {
    @Bindable var location: HomeLocationModel
    @Environment(AppModel.self) private var app
    @FocusState private var typing: Bool

    var body: some View {
        Form {
            Section {
                LabeledContent("Now", value: location.summary)
            } footer: {
                Text("Coco shows the weather for this place on the idle screen and when you ask about the weather without naming a place.")
            }
            Section {
                HStack {
                    TextField("City, and state or country", text: $location.query)
                        .textInputAutocapitalization(.words)
                        .autocorrectionDisabled()
                        .submitLabel(.search)
                        .focused($typing)
                        .onSubmit(find)
                    Button("Find", action: find)
                        .buttonStyle(.borderedProminent)
                        .disabled(!location.canFind)
                }
            } header: {
                Text("Change it")
            } footer: {
                Text("For example Portland, Oregon or Paris, France.")
            }
            if location.isBusy {
                Section { ProgressView().frame(maxWidth: .infinity) }
            }
            if case .failed(let message) = location.status {
                Section { Text(message).foregroundStyle(.red) }
            }
            if !location.choices.isEmpty {
                Section {
                    ForEach(location.choices, id: \.self) { place in
                        Button {
                            Task { await location.pick(place, using: app.hubClient) }
                        } label: {
                            HStack {
                                Text(place.label).foregroundStyle(.primary)
                                Spacer()
                                if location.isCurrent(place) {
                                    Image(systemName: "checkmark").foregroundStyle(.tint)
                                }
                            }
                        }
                        .disabled(location.isBusy)
                    }
                } header: {
                    Text("Saved. Not the right one?")
                } footer: {
                    if location.ambiguous {
                        Text("More than one place has that name. Tap the one you meant.")
                    }
                }
            }
            Section {
                Button("Use the Hub's default") {
                    Task { await location.useHubDefault(using: app.hubClient) }
                }
                .disabled(!location.canUseHubDefault)
            } footer: {
                Text("The location set in the Hub's config file on the Mac.")
            }
        }
        .navigationTitle("Home location")
    }

    private func find() {
        guard location.canFind else { return }
        typing = false
        Task { await location.find(using: app.hubClient) }
    }
}
