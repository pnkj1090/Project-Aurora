//
//  TestRunner.swift
//  ProjectAuroraTests
//
//  Runner for Phase 6K Swift Unit Tests.
//

import Foundation

@main
struct TestRunner {
    static func main() async {
        print("=== Phase 6K iOS Test Runner ===")
        do {
            let apiTests = AuroraAPIClientDisruptionTests()
            try await apiTests.runAllTests()

            let vmTests = await ScheduleDisruptionViewModelTests()
            try await vmTests.runAllTests()

            print("==================================================")
            print("ALL PHASE 6K TESTS PASSED SUCCESSFULLY!")
            print("==================================================")
        } catch {
            print("TEST SUITE FAILED: \(error)")
            exit(1)
        }
    }
}
