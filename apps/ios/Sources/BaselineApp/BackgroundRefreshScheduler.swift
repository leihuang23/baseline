import BaselineCore
import Foundation

#if os(iOS)
import BackgroundTasks
import UserNotifications

protocol BackgroundTaskScheduling: AnyObject {
    func register(
        forTaskWithIdentifier identifier: String,
        using queue: DispatchQueue?,
        launchHandler: @escaping (BGTask) -> Void
    ) -> Bool
    func submit(_ taskRequest: BGTaskRequest) throws
}

extension BGTaskScheduler: BackgroundTaskScheduling {}

protocol UserNotificationCentering: AnyObject {
    func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool
    func add(_ request: UNNotificationRequest) async throws
    func removePendingNotificationRequests(withIdentifiers: [String])
}

extension UNUserNotificationCenter: UserNotificationCentering {}

enum BackgroundRefreshScheduler {
    static let identifier = "com.baseline.ios.health-refresh"
    static let morningReminderIdentifier = "com.baseline.ios.morning-reminder"

    private static let state = SchedulerState()

    static func register(
        taskScheduler: any BackgroundTaskScheduling = BGTaskScheduler.shared,
        notificationCenter: any UserNotificationCentering = UNUserNotificationCenter.current(),
        syncHandler: @escaping () async -> Bool,
        wakeTimeProvider: @escaping () -> WakeTime
    ) async {
        await state.register(
            taskScheduler: taskScheduler,
            notificationCenter: notificationCenter,
            syncHandler: syncHandler,
            wakeTimeProvider: wakeTimeProvider
        )
    }

    static func schedule() {
        Task { await state.schedule() }
    }

    static func requestNotificationAuthorization() async -> Bool {
        await state.requestNotificationAuthorization()
    }

    static func scheduleMorningReminder() async {
        await state.scheduleMorningReminder()
    }
}

private actor SchedulerState {
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
            Task {
                await self.handle(task)
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
        Task {
            let didSync = await syncHandler?() ?? false
            task.setTaskCompleted(success: didSync)
        }
    }
}
#endif

func nextWakeDate(
    for wakeTime: WakeTime,
    now: Date = Date(),
    calendar: Calendar = .current
) -> Date {
    var components = calendar.dateComponents([.year, .month, .day], from: now)
    components.hour = wakeTime.hour
    components.minute = wakeTime.minute
    guard let candidate = calendar.date(from: components) else {
        return now.addingTimeInterval(60 * 60)
    }
    if candidate > now {
        return candidate
    }
    return calendar.date(byAdding: .day, value: 1, to: candidate)
        ?? candidate.addingTimeInterval(24 * 60 * 60)
}
