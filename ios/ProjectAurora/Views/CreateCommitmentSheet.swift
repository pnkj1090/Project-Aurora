import SwiftUI

public struct CreateCommitmentSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var viewModel: CommitmentViewModel

    @State private var title: String = ""
    @State private var description: String = ""
    @State private var priority: CommitmentPriority = .medium
    @State private var hasTargetDate: Bool = false
    @State private var targetDate: Date = Date().addingTimeInterval(86400 * 7)
    @State private var tagsString: String = ""
    @State private var isSubmitting: Bool = false

    public init(viewModel: CommitmentViewModel) {
        self.viewModel = viewModel
    }

    public var body: some View {
        NavigationStack {
            Form {
                Section("Commitment Details") {
                    TextField("Title (e.g. Promise to send proposal)", text: $title)
                    TextField("Description or details (optional)", text: $description, axis: .vertical)
                        .lineLimit(3...6)
                }

                Section("Priority") {
                    Picker("Priority", selection: $priority) {
                        ForEach(CommitmentPriority.allCases) { prio in
                            HStack {
                                Image(systemName: prio.systemIcon)
                                Text(prio.displayName)
                            }
                            .tag(prio)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                Section("Target Date") {
                    Toggle("Set Target Date", isOn: $hasTargetDate)
                    if hasTargetDate {
                        DatePicker("Target Date", selection: $targetDate, displayedComponents: [.date, .hourAndMinute])
                    }
                }

                Section("Tags") {
                    TextField("Tags (comma separated, e.g. team, project)", text: $tagsString)
                }
            }
            .navigationTitle("New Commitment")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }

                ToolbarItem(placement: .confirmationAction) {
                    Button("Create") {
                        Task {
                            isSubmitting = true
                            let tags = tagsString
                                .split(separator: ",")
                                .map { $0.trimmingCharacters(in: .whitespaces) }
                                .filter { !$0.isEmpty }

                            let success = await viewModel.createCommitment(
                                title: title,
                                description: description.isEmpty ? nil : description,
                                priority: priority,
                                targetDate: hasTargetDate ? targetDate : nil,
                                tags: tags
                            )
                            isSubmitting = false
                            if success {
                                dismiss()
                            }
                        }
                    }
                    .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty || isSubmitting)
                }
            }
        }
    }
}
