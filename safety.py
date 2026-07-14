RISK_WORDS = [
    "疼", "痛", "疼痛", "麻", "麻木",
    "头晕", "胸闷", "喘不过气", "呼吸困难",
    "摔倒", "恶心", "心慌", "不舒服",
    "难受", "手抬不起来", "肩膀痛", "胳膊痛",
    "手疼", "手痛", "很疼", "特别疼"
]

SAFE_NEGATIONS = [
    "没有不舒服", "没不舒服", "没有疼", "不疼", "没疼",
    "没有痛", "不痛", "没痛", "没有头晕", "不头晕",
    "没有胸闷", "不胸闷", "没有麻", "不麻", "没麻"
]


def has_risk(text: str) -> bool:
    """
    检测用户输入是否包含风险词。
    这是最高优先级。
    只要用户说疼痛、头晕、胸闷、不舒服，就立即停止训练。
    """
    text = text.strip()
    if any(word in text for word in SAFE_NEGATIONS):
        return False
    return any(word in text for word in RISK_WORDS)


def safety_stop_response(count=0, target=0, exercise_name="当前训练"):
    """
    生成安全停止回复。
    当用户出现不适时，不再进入普通对话和训练流程。
    安全提醒不交给大模型，避免语气被改得太轻。
    """
    return {
        "reply": "我们先马上停下来，不要继续这个动作。请坐稳，把手臂放松。如果不舒服的感觉还在，请及时联系家属或专业康复人员。",
        "state": "safety_stop",
        "exercise_name": exercise_name,
        "face": "serious",
        "motion": "stop",
        "count": count,
        "target": target,
        "show_continue": False
    }
