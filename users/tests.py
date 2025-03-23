from utils import is_valid_email, is_valid_name


def test_is_valid_name_pass() -> None:
    assert is_valid_name("NBEJ-8", "Angel Hill")


def test_is_valid_name_fail() -> None:
    assert not is_valid_name("XRTP-3", "Sam Smith")


def test_is_valid_email_pass() -> None:
    assert is_valid_email("denise.fernandez@example.org")


def test_is_valid_email_fail() -> None:
    assert not is_valid_email("user@example.com")


if __name__ == "__main__":
    test_is_valid_name_pass()
    test_is_valid_name_fail()
    test_is_valid_email_pass()
    test_is_valid_email_fail()
