import os

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

try:
    from openai import OpenAI
except ModuleNotFoundError:
    OpenAI = None

try:
    import anthropic
except ModuleNotFoundError:
    anthropic = None

if load_dotenv:
    # 运行脚本传入的安全配置优先于 .env，避免分享模式被本地文件覆盖。
    load_dotenv(override=False)
else:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                name, value = line.split("=", 1)
                os.environ[name.strip()] = value.strip().strip("'\"")

anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
openai_api_key = os.environ.get("OPENAI_API_KEY")

anthropic_client = (
    anthropic.Anthropic(api_key=anthropic_api_key, timeout=15.0, max_retries=1)
    if anthropic and anthropic_api_key
    else None
)
openai_client = (
    OpenAI(api_key=openai_api_key, timeout=15.0, max_retries=1)
    if OpenAI and openai_api_key
    else None
)

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")

SYSTEM_PROMPT = """
你是一个可爱的桌面康复训练陪伴机器人，服务对象是老年人、残疾人和运动功能障碍人群。

你的任务不是诊断疾病，也不是制定医疗方案。
你的任务是把系统已经决定好的康复训练话术，改写得更自然、更温柔、更可爱，更适合机器人语音播报。

重要规则：
1. 不改变训练动作名称。
2. 不改变训练次数。
3. 不新增训练动作。
4. 不提供诊断、用药、治疗建议。
5. 不说“保证恢复”“一定有效”“你很快就会好”等夸大表述。
6. 如果原话包含疼痛、不舒服、停止训练、安全提醒，必须完整保留这些意思。
7. 语气要像一个温柔、可爱、耐心的小机器人。
8. 句子要短，适合语音朗读。
9. 可以适当使用“我们慢慢来”“我陪着你”“不着急”“安全最重要”这类表达。
10. 输出只返回中文话术，不要解释，不要输出 JSON。
"""


def _log_llm_failure(provider: str, task: str, error: Exception) -> None:
    status = getattr(error, "status_code", None)
    code = getattr(error, "code", None)

    details = []
    if status:
        details.append(f"status={status}")
    if code:
        details.append(f"code={code}")

    suffix = f"（{', '.join(details)}）" if details else ""
    print(f"{provider} {task}失败，已使用本地兜底话术{suffix}。")


def _anthropic_reply(system_prompt: str, user_prompt: str) -> str:
    response = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=300,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return "".join(
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ).strip()


def _openai_reply(system_prompt: str, user_prompt: str) -> str:
    response = openai_client.responses.create(
        model=OPENAI_MODEL,
        instructions=system_prompt,
        input=user_prompt,
    )

    return response.output_text.strip()


def _llm_reply(system_prompt: str, user_prompt: str, fallback: str, task: str) -> str:
    if openai_client:
        try:
            return _openai_reply(system_prompt, user_prompt) or fallback
        except Exception as e:
            _log_llm_failure("OpenAI", task, e)
            return fallback

    if anthropic_client:
        try:
            return _anthropic_reply(system_prompt, user_prompt) or fallback
        except Exception as e:
            _log_llm_failure("Claude", task, e)
            return fallback

    return fallback


def polish_reply(original_reply: str, state: str = "", exercise_name: str = "") -> str:
    """
    用大模型把固定话术润色得更自然、更可爱。
    大模型只负责“怎么说”，不负责“做什么训练”。
    """

    return _llm_reply(
        SYSTEM_PROMPT,
        f"""
当前状态：{state}
当前训练：{exercise_name}
原始话术：{original_reply}

请把原始话术改写成更自然、更温柔、更可爱的康复陪练机器人语气。
""",
        original_reply,
        "润色",
    )


CASUAL_PROMPT = """
你是一个自然、温柔、有情绪回应的康复训练陪伴机器人。
用户可以问任何日常问题、闲聊、表达情绪，也可以随时回到康复训练。
你要像正常陪伴对话一样先接住用户的话，不要每句都急着拉回训练。

规则：
1. 不诊断疾病，不给用药或治疗建议。
2. 不承诺恢复效果。
3. 回复适合语音播报，通常 1 到 3 句。
4. 如果用户问的是百科、生活、心情、家常问题，要根据问题本身回答，不要使用模板话术。
5. 如果用户问实时信息，例如天气、新闻、时间，而你无法实时查看，要诚实说明无法实时查看，并给出可执行建议。
6. 只有用户明确提到训练、休息、继续、安全或当前动作时，才自然引导回训练；普通闲聊可以不提训练。
7. 只输出中文话术。
"""


def casual_reply(user_text: str, state: str = "", exercise_name: str = "") -> str:
    """
    处理训练外的闲聊：允许自然回应，但把话题拉回训练主线。
    没有大模型 SDK 或 API key 时使用本地兜底话术。
    """

    if any(word in user_text for word in ["你好", "嗨", "hello", "Hello", "在吗"]):
        fallback = "你好呀，我在这里。你想聊什么都可以，我会慢慢听。"
    elif any(word in user_text for word in ["难过", "烦", "心情不好", "不开心", "累"]):
        fallback = "听起来今天有点不容易。没关系，我们先慢慢来，你可以跟我说说发生了什么。"
    elif "天气" in user_text:
        fallback = "我现在不能实时查看天气。你可以看一下手机天气，如果要出门，记得按温度添减衣服。"
    else:
        fallback = "我听到了。你可以继续说，我会陪你慢慢聊。"

    return _llm_reply(
        CASUAL_PROMPT,
        f"""
当前状态：{state}
当前训练：{exercise_name}
用户说：{user_text}

请先针对用户问题作答，再自然拉回康复训练主线。
""",
        fallback,
        "闲聊",
    )
