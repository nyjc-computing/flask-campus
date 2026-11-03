"""Unit tests for campus.flask_campus.parameter argument predicates."""

import inspect
import unittest

from campus.flask_campus import parameter, utils


def _sink(user_id: str, **rest: object) -> tuple[str, dict]:
    """Stub handler accepting extra arguments via **kwargs."""
    return user_id, rest


class TestIsKeywordSupported(unittest.TestCase):
    """A **kwargs parameter can receive keyword arguments; *args cannot."""

    def test_var_keyword_is_supported(self):
        def fn(**kwargs: object) -> None:
            pass

        param = inspect.signature(fn).parameters["kwargs"]
        self.assertTrue(parameter.is_keyword_supported(param))

    def test_var_positional_is_not_supported(self):
        def fn(*args: object) -> None:
            pass

        param = inspect.signature(fn).parameters["args"]
        self.assertFalse(parameter.is_keyword_supported(param))

    def test_unpack_request_accepts_kwargs_handler(self):
        """Decoration must not reject handlers that declare **kwargs."""
        wrapped = utils.unpack_request(_sink)
        self.assertTrue(callable(wrapped))


if __name__ == "__main__":
    unittest.main()
