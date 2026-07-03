#if os(iOS)
import BaselineCore
import Foundation
import HealthKit

final class HealthKitClient: HealthAuthorizationClient, HealthKitReading, @unchecked Sendable {
    private let store = HKHealthStore()
    var enabledCategories = Set(HealthCategory.allCases)

    func requestAuthorization(for categories: [HealthCategory]) async throws -> Set<HealthCategory> {
        guard HKHealthStore.isHealthDataAvailable() else {
            return []
        }
        let typesByCategory = Dictionary(
            uniqueKeysWithValues: categories.compactMap { category in
                Self.objectType(for: category).map { (category, $0) }
            }
        )
        let types = Set(typesByCategory.values)
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            store.requestAuthorization(toShare: [], read: types) { success, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    success ? continuation.resume() : continuation.resume(returning: ())
                }
            }
        }
        // HealthKit intentionally does not expose read-authorization status. An empty
        // query can mean either "authorized but no history" or "not authorized", so a
        // successful prompt keeps every requestable category enabled for future syncs.
        return Set(typesByCategory.keys)
    }

    func readSamples(
        for category: HealthCategory,
        anchorData: Data?
    ) async throws -> HealthKitReadResult {
        guard enabledCategories.contains(category),
              let objectType = Self.objectType(for: category) else {
            return HealthKitReadResult(category: category, samples: [], newAnchorData: anchorData)
        }
        let anchor = try decodeAnchor(anchorData)
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: objectType,
                predicate: nil,
                anchor: anchor,
                limit: HKObjectQueryNoLimit
            ) { _, samples, _, newAnchor, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                do {
                    let mapped = try (samples ?? []).compactMap { sample in
                        try Self.map(sample: sample, category: category)
                    }
                    continuation.resume(
                        returning: HealthKitReadResult(
                            category: category,
                            samples: mapped,
                            newAnchorData: try encodeAnchor(newAnchor)
                        )
                    )
                } catch {
                    continuation.resume(throwing: error)
                }
            }
            store.execute(query)
        }
    }

    private static func objectType(for category: HealthCategory) -> HKSampleType? {
        switch category {
        case .sleep:
            HKObjectType.categoryType(forIdentifier: .sleepAnalysis)
        case .workouts:
            HKObjectType.workoutType()
        case .steps:
            HKObjectType.quantityType(forIdentifier: .stepCount)
        case .heartRateVariability:
            HKObjectType.quantityType(forIdentifier: .heartRateVariabilitySDNN)
        case .restingHeartRate:
            HKObjectType.quantityType(forIdentifier: .restingHeartRate)
        case .vo2Max:
            HKObjectType.quantityType(forIdentifier: .vo2Max)
        }
    }

    private static func map(sample: HKSample, category: HealthCategory) throws -> HealthSample? {
        if let quantitySample = sample as? HKQuantitySample {
            let unit = unitForQuantity(category)
            return HealthSample(
                sourceSampleID: quantitySample.uuid.uuidString,
                sampleType: category.apiSampleType,
                startTime: quantitySample.startDate,
                endTime: quantitySample.endDate,
                value: quantitySample.quantity.doubleValue(for: unit.hkUnit),
                unit: unit.apiUnit,
                sourceMetadata: ["source": "healthkit", "category": category.rawValue]
            )
        }
        if let categorySample = sample as? HKCategorySample, category == .sleep {
            guard var sourceMetadata = HealthKitSleepAnalysisMetadata.asleepMetadata(
                forRawValue: categorySample.value
            ) else {
                return nil
            }
            sourceMetadata["source"] = "healthkit"
            sourceMetadata["category"] = category.rawValue
            return HealthSample(
                sourceSampleID: categorySample.uuid.uuidString,
                sampleType: category.apiSampleType,
                startTime: categorySample.startDate,
                endTime: categorySample.endDate,
                value: categorySample.endDate.timeIntervalSince(categorySample.startDate) / 3600,
                unit: "h",
                sourceMetadata: sourceMetadata
            )
        }
        if let workout = sample as? HKWorkout, category == .workouts {
            return HealthSample(
                sourceSampleID: workout.uuid.uuidString,
                sampleType: category.apiSampleType,
                startTime: workout.startDate,
                endTime: workout.endDate,
                value: workout.duration / 60,
                unit: "min",
                sourceMetadata: [
                    "source": "healthkit",
                    "category": category.rawValue,
                    "workout_activity_type": String(workout.workoutActivityType.rawValue),
                ]
            )
        }
        return nil
    }

    private static func unitForQuantity(_ category: HealthCategory) -> (hkUnit: HKUnit, apiUnit: String) {
        switch category {
        case .steps:
            (.count(), "count")
        case .heartRateVariability:
            (.secondUnit(with: .milli), "ms")
        case .restingHeartRate:
            (.count().unitDivided(by: .minute()), "count/min")
        case .vo2Max:
            (
                .literUnit(with: .milli)
                    .unitDivided(by: .gramUnit(with: .kilo))
                    .unitDivided(by: .minute()),
                "mL/kg/min"
            )
        case .sleep, .workouts:
            (.count(), "count")
        }
    }
}

private func decodeAnchor(_ data: Data?) throws -> HKQueryAnchor? {
    guard let data else {
        return nil
    }
    return try NSKeyedUnarchiver.unarchivedObject(
        ofClass: HKQueryAnchor.self,
        from: data
    )
}

private func encodeAnchor(_ anchor: HKQueryAnchor?) throws -> Data? {
    guard let anchor else {
        return nil
    }
    return try NSKeyedArchiver.archivedData(
        withRootObject: anchor,
        requiringSecureCoding: true
    )
}
#endif
