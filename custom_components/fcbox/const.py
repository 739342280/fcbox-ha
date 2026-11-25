"""Constants for the 丰巢快递柜 integration."""

DOMAIN = "fcbox"

CONF_FC_USER_FLAG = "fc_user_flag"
CONF_FC_USER_AUTH = "fc_user_auth"
CONF_AUTHORIZATION = "authorization"
CONF_MOBILE = "mobile"
CONF_VERIFY_CODE = "verify_code"

BASE_URL = "https://consumer.fcbox.com"
API_URL = BASE_URL + "/post/express/toPickList"
API_BODY = "pageNo=1&pageSize=50&channelCode=ANDROID_FC_APP"

# 丰巢 App 的固定 opCode（签名校验未启用，sign 任意非空即可）
OP_CODE = "9ab71554b5c04565bdbfda71e15a54fe"

USER_AGENT = (
    "channel=xiaomi,ip=192.168.100.179,os=17,deviceType=25098PN5AC,"
    "platform=Android,resolution=1220*2441,versionCode=6011000"
)

UPDATE_INTERVAL_MINUTES = 15
