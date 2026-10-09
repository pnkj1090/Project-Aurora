import SwiftUI

public struct CreateMemorySheet: View {
    @ObservedObject var viewModel: MemoryViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var content: String = ""
    @State private var selectedType: MemoryType = .explicit
    @State private var importance: Int = 3
    @State private var tagsText: String = ""
    @State private var hasExpiration: Bool = false
    @State private var expirationDate: Date = Calendar.current.date(byAdding: .day, value: 7, to: Date()) ?? Date()
    @State private var isSubmitting: Bool = false

    public init(viewModel: MemoryViewModel) {
        self.viewModel = viewModel
    }

    public var body: some View {
        NavigationStack {
            Form {
                Section("Memory Content") {
                    TextEditor(text: $content)
                        .frame(minHeight: 100)
                }

                Section("Classification") {
                    Picker("Type", selection: $selectedType) {
                        ForEach(MemoryType.allCases) { type in
                            Label(type.displayName, systemImage: type.systemIcon)
                                .tag(type)
                        }
                    }

                    Stepper(value: $importance, in: 1...5) {
                        HStack {
                            Text("Importance")
                            Spacer()
                            HStack(spacing: 2) {
                                ForEach(1...5, id: \.self) { i in
                                    Image(systemName: i <= importance ? "star.fill" : "star")
                                        .foregroundColor(i <= importance ? .yellow : .secondary)
                                        .font(.caption)
                                }
                            }
                        }
                    }
                }

                Section("Tags") {
                    TextField("e.g. work, meetings, schedule (comma separated)", text: $tagsText)
                }

                Section("Expiration") {
                    Toggle("Set Expiration", isOn: $hasExpiration)
                    if hasExpiration {
                        DatePicker("Expires on", selection: $expirationDate, displayedComponents: [.date, .hourAndMinute])
                    }
                }
            }
            .navigationTitle("New Memory")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            await saveMemory()
                        }
                    }
                    .disabled(content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSubmitting)
                }
            }
        }
    }

    private func saveMemory() async {
        isSubmitting = true
        let tags = tagsText
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }

        let success = await viewModel.createMemory(
            content: content,
            type: selectedType,
            importance: importance,
            tags: tags,
            expiresAt: hasExpiration ? expirationDate : nil
        )

        isSubmitting = false
        if success {
            dismiss()
        }
    }
}
