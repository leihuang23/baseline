import BaselineCore
import Foundation

#if os(iOS)
import BackgroundTasks
import UserNotifications

/// Tracks whether a background task has already been completed so that
/// `setTaskCompleted(success:)` is called exactly once, even if the OS
/// expiration handler fires after the sync work finishes (or vice versa).
private final class TaskCompletionTracker: @unchecked Sendable {
    private let lock = NSLock()
    private var completed = false

    func complete(task: BGTask, success: Bool) {
        lock.lock()
        defer { lock.unlock() }
        guard !completed else { return }
        completed = true
        task.setTaskCompleted(success: success)
    }
}

protocol BackgroundTaskScheduling: AnyObject, Sendable {
    func register(
        forTaskWithIdentifier identifier: String,
        using queue: DispatchQueue?,
        launchHandler: @escaping (BGTask) -> Void
    ) -> Bool
    func submit(_ taskRequest: BGTaskRequest) throws
}

extension BGTaskScheduler: @retroactive @unchecked Sendable {}
extension BGTaskScheduler: BackgroundTaskScheduling {}

protocol UserNotificationCentering: AnyObject, Sendable {
    func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool
    func add(_ request: UNNotificationRequest) async throws
    func removePendingNotificationRequests(withIdentifiers: [String])
}

extension UNUserNotificationCenter: @retroactive @unchecked Sendable {}
extension UNUserNotificationCenter: UserNotificationCentering {}

@MainActor
enum BackgroundRefreshScheduler {
    static let identifier = "com.baseline.ios.health-refresh"
    static let morningReminderIdentifier = "com.baseline.ios.morning-reminder"

    private static let state = SchedulerState()

    static func register(
        taskScheduler: any BackgroundTaskScheduling = BGTaskScheduler.shared,
        notificationCenter: any UserNotificationCentering = UNUserNotificationCenter.current(),
        syncHandler: @escaping () async -> Bool,
        wakeTimeProvider: @escaping () -> WakeTime
    ) {
        state.register(
            taskScheduler: taskScheduler,
            notificationCenter: notificationCenter,
            syncHandler: syncHandler,
            wakeTimeProvider: wakeTimeProvider
        )
    }

    static func schedule() {
        state.schedule()
    }

    static func requestNotificationAuthorization() async -> Bool {
        await state.requestNotificationAuthorization()
    }

    static func scheduleMorningReminder() async {
        await state.scheduleMorningReminder()
    }

    #if DEBUG
    /// Resets the singleton scheduler state so tests that inject mocks do not
    /// leave mutated state behind for subsequent tests.
    static func resetForTesting() {
        state.resetForTesting()
    }
    #endif
}

@MainActor
private final class SchedulerState {
    private var taskScheduler: (any BackgroundTaskScheduling)?
    private var notificationCenter: (any UserNotificationCentering)?
    private var syncHandler: (() async -> Bool)?
    private var wakeTimeProvider: (() -> WakeTime)?

    func register(
        taskScheduler: any BackgroundTaskScheduling,
        notificationCenter: any UserNotificationCentering,
        syncHandler: @escaping () async -> Bool,
        wakeTimeProvider: @escaping () -> WakeTime
    ) {
        self.taskScheduler = taskScheduler
        self.notificationCenter = notificationCenter
        self.syncHandler = syncHandler
        self.wakeTimeProvider = wakeTimeProvider

        taskScheduler.register(
            forTaskWithIdentifier: BackgroundRefreshScheduler.identifier,
            using: nil
        ) { task in
            Task { @MainActor in
                self.handle(task)
            }
        }
    }

    func schedule() {
        guard let taskScheduler, let wakeTimeProvider else {
            return
        }
        let request = BGAppRefreshTaskRequest(identifier: BackgroundRefreshScheduler.identifier)
        request.earliestBeginDate = nextWakeDate(for: wakeTimeProvider())
        try? taskScheduler.submit(request)
    }

    func requestNotificationAuthorization() async -> Bool {
        guard let notificationCenter else {
            return false
        }
        do {
            return try await notificationCenter.requestAuthorization(options: [.alert, .sound])
        } catch {
            return false
        }
    }

    func scheduleMorningReminder() async {
        guard let notificationCenter, let wakeTimeProvider else {
            return
        }
        let wakeTime = wakeTimeProvider()
        let content = UNMutableNotificationContent()
        content.title = "Baseline"
        content.body = "Time for your morning sync and check-in."
        content.sound = .default
        let components = DateComponents(hour: wakeTime.hour, minute: wakeTime.minute)
        let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: true)
        let request = UNNotificationRequest(
            identifier: BackgroundRefreshScheduler.morningReminderIdentifier,
            content: content,
            trigger: trigger
        )
        notificationCenter.removePendingNotificationRequests(
            withIdentifiers: [BackgroundRefreshScheduler.morningReminderIdentifier]
        )
        try? await notificationCenter.add(request)
    }

    private func handle(_ task: BGTask) {
        schedule()
        guard task is BGAppRefreshTask else {
            task.setTaskCompleted(success: false)
            return
        }
        let tracker = TaskCompletionTracker()
        task.expirationHandler = {
            tracker.complete(task: task, success: false)
        }
        Task {
            let didSync = await syncHandler?() ?? false
            tracker.complete(task: task, success: didSync)
        }
    }

    #if DEBUG
    func resetForTesting() {
        taskScheduler = nil
        notificationCenter = nil
        syncHandler = nil
        wakeTimeProvider = nil
    }
    #endif
}
#endif

func nextWakeDate(
    for wakeTime: WakeTime,
    now: Date = Date(),
    calendar: Calendar = .current
) -> Date {
    nextWakeDate(
        for: wakeTime,
        now: now,
        calendar: calendar,
        dateFromComponents: calendar.date(from:)
    )
}

/// Internal overload that injects the calendar-to-date conversion so the
/// defensive `calendar.date(from:) == nil` fallback can be exercised in tests.
internal func nextWakeDate(
    for wakeTime: WakeTime,
    now: Date,
    calendar: Calendar,
    dateFromComponents: (DateComponents) -> Date?
) -> Date {
    var components = calendar.dateComponents([.year, .month, .day], from: now)
    components.hour = wakeTime.hour
    components.minute = wakeTime.minute
    guard let candidate = dateFromComponents(components) else {
        return now.addingTimeInterval(60 * 60)
    }
    if candidate > now {
        return candidate
    }
    return calendar.date(byAdding: .day, value: 1, to: candidate)
        ?? candidate.addingTimeInterval(24 * 60 * 60)
}
