#if os(iOS)
import BackgroundTasks
import Foundation

enum BackgroundRefreshScheduler {
    static let identifier = "com.baseline.ios.health-refresh"
    private static var syncHandler: (() async -> Bool)?

    static func register(syncHandler: @escaping () async -> Bool) {
        self.syncHandler = syncHandler
        BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: nil) { task in
            handle(task)
        }
    }

    static func schedule() {
        let request = BGAppRefreshTaskRequest(identifier: identifier)
        request.earliestBeginDate = Date().addingTimeInterval(60 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }

    private static func handle(_ task: BGTask) {
        schedule()
        guard task is BGAppRefreshTask else {
            task.setTaskCompleted(success: false)
            return
        }
        let operation = Task {
            let didSync = await syncHandler?() ?? false
            task.setTaskCompleted(success: didSync)
        }
        task.expirationHandler = {
            operation.cancel()
        }
    }
}
#endif
