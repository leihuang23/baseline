import Foundation

public protocol TokenStoring: Sendable {
    func saveToken(_ token: String, account: String) throws
    func loadToken(account: String) throws -> String?
    func deleteToken(account: String) throws
}

public enum TokenStoreError: Error, Equatable, Sendable {
    case keychainUnavailable
    case unexpectedStatus(Int32)
    case invalidStoredData
}

#if canImport(Security)
import Security

public final class KeychainTokenStore: TokenStoring {
    private let service: String

    public init(service: String = "com.baseline.ios") {
        self.service = service
    }

    public func saveToken(_ token: String, account: String) throws {
        let data = Data(token.utf8)
        let query = baseQuery(account: account)
        SecItemDelete(query as CFDictionary)
        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(attributes as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw TokenStoreError.unexpectedStatus(status)
        }
    }

    public func loadToken(account: String) throws -> String? {
        var query = baseQuery(account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess else {
            throw TokenStoreError.unexpectedStatus(status)
        }
        guard let data = result as? Data, let token = String(data: data, encoding: .utf8) else {
            throw TokenStoreError.invalidStoredData
        }
        return token
    }

    public func deleteToken(account: String) throws {
        let status = SecItemDelete(baseQuery(account: account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw TokenStoreError.unexpectedStatus(status)
        }
    }

    private func baseQuery(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}
#else
public final class KeychainTokenStore: TokenStoring {
    public init(service: String = "com.baseline.ios") {}

    public func saveToken(_ token: String, account: String) throws {
        throw TokenStoreError.keychainUnavailable
    }

    public func loadToken(account: String) throws -> String? {
        throw TokenStoreError.keychainUnavailable
    }

    public func deleteToken(account: String) throws {
        throw TokenStoreError.keychainUnavailable
    }
}
#endif
