import unittest

from irbis_control.infrastructure.irbis_bridge import IrbisClient, IrbisError


class RetryClient(IrbisClient):
    def __init__(self, failures: int, error: str = "временная ошибка") -> None:
        super().__init__(
            "127.0.0.1",
            connection_attempts=3,
            retry_delay=0,
        )
        self.failures = failures
        self.error = error
        self.calls = 0

    def _register_once(self) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise IrbisError(self.error)
        self.registered = True


    def test_temporary_failure_is_retried(self) -> None:
        client = RetryClient(failures=2)

        client.register()

        self.assertTrue(client.registered)
        self.assertEqual(3, client.calls)

    def test_authentication_failure_is_not_retried(self) -> None:
        client = RetryClient(failures=3, error="ИРБИС отклонил вход. Код: -3337")

        with self.assertRaises(IrbisError):
            client.register()

        self.assertEqual(1, client.calls)


if __name__ == "__main__":
    unittest.main()
