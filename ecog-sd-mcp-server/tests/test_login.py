from ecog_sd_mcp.login import _is_captcha


def test_is_captcha_detects_markers():
    assert _is_captcha("<div>Please verify you are human</div>") is True
    assert _is_captcha("<script src='recaptcha/api.js'>") is True


def test_is_captcha_false_on_normal_page():
    assert _is_captcha("<input id='password' type='password'>") is False
