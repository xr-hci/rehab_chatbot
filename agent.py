import json
from safety import has_risk, safety_stop_response
from llm import casual_reply, polish_reply


POSITIVE_WORDS = [
    "可以", "好", "好的", "开始", "愿意", "行", "来吧", "试试",
    "没问题", "可以开始", "想练", "训练", "继续"
]

NEXT_GROUP_COMMAND = "泡泡完成"
NEXT_GROUP_COMMANDS = [
    "完成", "完成了", "说完成", "我完成了", "做好了", "做完了",
    "下一组", "下一个", "下一轮", "下一次",
    "泡泡完成", "泡泡完成了", "泡泡完了",
    "宝宝完成", "宝宝完成了", "宝宝完了",
    "抱抱完成", "抱抱完成了", "抱抱完了",
    "泡泡", "宝宝", "抱抱"
]
PRAISE_FACES = ["praise", "smile", "wink", "proud", "spark"]

NEGATIVE_WORDS = [
    "不想", "不练", "不要", "算了", "今天不", "不愿意", "不想练"
]

NO_RISK_WORDS = [
    "没有不舒服", "没有疼", "没有痛", "不疼", "不痛", "没疼",
    "没有头晕", "没有胸闷", "没有麻", "没有", "不麻", "没问题", "正常"
]

EXERCISE_KEYWORDS = {
    "wrist_flex_extend": ["手腕", "腕", "屈伸", "抬腕"],
    "table_touch": ["触点", "桌面", "左右", "协调"],
    "finger_open_close": ["手指", "张合", "握拳", "手部"]
}


def _has_any(text, words):
    return any(word in text for word in words)


def _compact_text(text):
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def is_question(text: str) -> bool:
    return _has_any(text, [
        "吗", "呢", "什么", "为什么", "怎么", "怎样", "多少",
        "能不能", "可不可以", "是不是", "有没有", "?", "？"
    ])


def is_explicit_training_request(text: str) -> bool:
    return _has_any(text, ["想练", "要练", "开始练", "练一下", "做一下", "做训练", "开始训练"])


def infer_intent(text: str) -> str:
    """
    把自然语言先归一成训练意图。
    安全风险仍由 safety.py 最高优先级处理；这里只判断普通控制意图。
    """
    text = text.strip()

    if not text:
        return "empty"

    if _has_any(text, ["结束", "不练了", "不想练了", "停止训练", "今天到这", "算了"]):
        return "end"

    if _has_any(text, ["再说", "重复", "没听清", "怎么做", "不会", "讲一遍", "说明"]):
        return "repeat"

    compact_text = _compact_text(text)
    if _has_any(compact_text, NEXT_GROUP_COMMANDS):
        return "complete"

    if _has_any(text, ["继续", "接着", "可以继续", "还能练", "再来", "往下", "下一次"]):
        return "continue"

    if _has_any(text, ["休息", "累", "暂停", "缓一缓", "歇", "等一下", "慢一点"]):
        return "rest"

    if _has_any(text, NEGATIVE_WORDS):
        return "decline"

    if _has_any(text, NO_RISK_WORDS):
        return "no_risk"

    if _has_any(text, ["天气", "心情", "家里", "吃饭", "睡觉", "电视", "新闻", "聊天"]):
        return "chat"

    if is_question(text):
        return "chat"

    if _has_any(text, ["想练", "训练", "开始", "来吧", "试试", "可以开始", "好的", "行吧", "愿意"]):
        return "start"

    return "chat"


class RehabChatAgent:
    """
    对话式康复训练智能体。

    它不是普通聊天机器人，而是一个状态机：
    1. 先问候用户
    2. 询问是否训练
    3. 训练前安全确认
    4. 指导训练
    5. 训练中计数和鼓励
    6. 休息/继续/结束
    7. 疼痛或不适时安全停止

    大模型只负责润色话术，不负责决定训练动作、次数和安全规则。
    """

    def __init__(self, exercise_file="exercises.json"):
        with open(exercise_file, "r", encoding="utf-8") as f:
            self.exercises = json.load(f)

        self.state = "idle"
        self.current_exercise = None
        self.count = 0
        self.rest_count = 0
        self.pain_reported = False

    def _base_response(self, reply, face="care", motion="idle", show_continue=True, use_llm=True):
        """
        统一返回格式。
        前端网页会根据这些字段更新表情、文字、状态、进度。

        use_llm=True 时，会调用大模型把话术润色得更自然、更可爱。
        但训练状态、动作、次数和安全规则仍然由 Python 控制。
        """
        exercise_name = self.current_exercise["name"] if self.current_exercise else "未开始"
        target = self.current_exercise["reps"] if self.current_exercise else 0

        if use_llm:
            reply = polish_reply(
                original_reply=reply,
                state=self.state,
                exercise_name=exercise_name
            )

        return {
            "reply": reply,
            "state": self.state,
            "exercise_name": exercise_name,
            "face": face,
            "motion": motion,
            "count": self.count,
            "target": target,
            "show_continue": show_continue
        }

    def _choose_exercise(self, exercise_id="finger_open_close"):
        """
        从训练动作库中选择训练。
        第一版默认选择手指张合训练。
        后续可以根据用户情况或治疗师方案选择不同训练。
        """
        for ex in self.exercises:
            if ex["exercise_id"] == exercise_id:
                return ex
        return self.exercises[0]

    def _exercise_from_text(self, text):
        for exercise_id, keywords in EXERCISE_KEYWORDS.items():
            if _has_any(text, keywords):
                return self._choose_exercise(exercise_id)
        return None

    def _current_motion(self):
        return self.current_exercise["motion"] if self.current_exercise else "idle"

    def _exercise_instruction(self):
        steps = " ".join(self.current_exercise["steps"][:3])
        return (
            f"{self.current_exercise['name']}，一共{self.current_exercise['reps']}组。"
            f"{steps} 做完说“{NEXT_GROUP_COMMAND}”。不舒服马上停。"
        )

    def _start_selected_exercise(self, exercise=None):
        self.current_exercise = exercise or self.current_exercise or self._choose_exercise("finger_open_close")
        self.count = 0
        self.state = "training"
        return self._base_response(
            self._exercise_instruction(),
            face="spark",
            motion=self._current_motion()
        )

    def _chat_then_redirect(self, text, face="care"):
        exercise_name = self.current_exercise["name"] if self.current_exercise else "未开始"
        reply = casual_reply(text, state=self.state, exercise_name=exercise_name)
        return self._base_response(
            reply,
            face=face,
            motion=self._current_motion() if self.state in ["instruction", "training"] else "idle",
            use_llm=False
        )

    def welcome(self):
        """
        打开网页后，机器人主动问候。
        """
        self.state = "ask_training"
        self.current_exercise = None
        self.count = 0
        self.rest_count = 0
        self.pain_reported = False

        return self._base_response(
            "你好呀。准备好了就说“开始”，我带你动一动手。",
            face="spark",
            motion="demo_hand"
        )

    def handle_input(self, user_text: str):
        """
        处理用户输入。
        每次用户点击按钮或输入文字，都会进入这里。
        """
        text = user_text.strip()

        if not text:
            return self._base_response("我在这里。你可以说：我想训练，或者今天先休息。")

        intent = infer_intent(text)

        # 主动提醒：由前端定时器触发，让机器人更主动
        if text.startswith("主动提醒"):
            if self.state == "training":
                return self._base_response(
                    f"我看到你停了一会儿。没关系，我们不用着急。准备好以后，说“{NEXT_GROUP_COMMAND}”就可以继续计数；如果累了，也可以先休息。",
                    face="care",
                    motion=self.current_exercise["motion"] if self.current_exercise else "idle"
                )

            if self.state == "rest":
                return self._base_response(
                    "已经休息一会儿了。你现在感觉可以继续吗？如果还累，我们也可以结束今天的训练。",
                    face="rest",
                    motion="idle"
                )

            if self.state == "ask_training":
                return self._base_response(
                    "今天可以只做一小组很轻松的训练。你愿意试一试吗？如果不想练，也可以先休息。",
                    face="care",
                    motion="wave"
                )

            return self._base_response(
                "我会在这里陪着你。准备好了，我们可以做一点轻松的康复训练。",
                face="care",
                motion="idle"
            )

        # 1. 最高优先级：安全检查
        # 只要用户说疼痛、头晕、胸闷、不舒服，就直接停止训练。
        if has_risk(text):
            self.state = "safety_stop"
            self.pain_reported = True
            exercise_name = self.current_exercise["name"] if self.current_exercise else "当前训练"
            target = self.current_exercise["reps"] if self.current_exercise else 0
            return safety_stop_response(
                count=self.count,
                target=target,
                exercise_name=exercise_name
            )

        # 2. 待机状态：进入问候
        if self.state == "idle":
            exercise = self._exercise_from_text(text)
            if is_question(text) and not is_explicit_training_request(text):
                return self._chat_then_redirect(text)

            if intent in ["start", "no_risk"] or (exercise and is_explicit_training_request(text)):
                if exercise:
                    self.current_exercise = exercise
                return self._start_selected_exercise(self.current_exercise)
            if intent == "chat":
                return self._chat_then_redirect(text)
            return self.welcome()

        # 3. 询问是否愿意训练
        if self.state == "ask_training":
            exercise = self._exercise_from_text(text)
            if is_question(text) and not is_explicit_training_request(text):
                return self._chat_then_redirect(text)

            if exercise and is_explicit_training_request(text):
                self.current_exercise = exercise

            if intent == "decline":
                self.state = "idle"
                return self._base_response(
                    "好的，今天可以先休息。康复训练不需要勉强，感觉合适的时候再开始。",
                    face="rest",
                    motion="idle"
                )

            if intent in ["start", "continue", "no_risk"] or (exercise and is_explicit_training_request(text)):
                return self._start_selected_exercise(self.current_exercise)

            return self._base_response(
                casual_reply(text, state=self.state, exercise_name="未开始"),
                face="care",
                motion="idle",
                use_llm=False
            )

        # 4. 训练前安全确认
        if self.state == "pre_check":
            exercise = self._exercise_from_text(text)
            if intent == "chat":
                return self._chat_then_redirect(text)

            if exercise and is_explicit_training_request(text):
                self.current_exercise = exercise

            if intent in ["decline", "end", "rest"]:
                self.state = "idle"
                return self._base_response(
                    "好的，我们先不开始。你可以休息一会儿，感觉合适时再叫我。",
                    face="rest",
                    motion="idle"
                )

            if intent in ["no_risk", "start", "continue"] or "没问题" in text:
                return self._start_selected_exercise(self.current_exercise)

            return self._base_response(
                "先确认安全：没有不舒服就说“没有”。",
                face="thinking",
                motion="demo_hand"
            )

        # 5. 动作说明状态
        if self.state == "instruction":
            exercise = self._exercise_from_text(text)
            if is_question(text) and not is_explicit_training_request(text):
                return self._chat_then_redirect(text)

            if exercise and exercise != self.current_exercise and is_explicit_training_request(text):
                return self._start_selected_exercise(exercise)

            if intent in ["start", "continue", "no_risk"]:
                self.state = "training"
                return self._base_response(
                    f"开始。做完一组就说“{NEXT_GROUP_COMMAND}”。",
                    face="care",
                    motion=self._current_motion()
                )

            if intent == "repeat":
                return self._base_response(
                    f"我再说一遍：{self._exercise_instruction()}",
                    face="care",
                    motion=self._current_motion()
                )

            if intent == "rest":
                return self.rest("好的，我们先休息一下。准备好后直接说继续。")

            if intent in ["end", "decline"]:
                return self.summary()

            if intent == "chat":
                return self._chat_then_redirect(text)

            return self._base_response(
                "准备好后说开始就行。没听清也可以让我再讲一遍。",
                face="care",
                motion=self._current_motion()
            )

        # 6. 正在训练
        if self.state == "training":
            exercise = self._exercise_from_text(text)
            if is_question(text) and not is_explicit_training_request(text):
                return self._chat_then_redirect(text)

            if exercise and exercise != self.current_exercise and is_explicit_training_request(text):
                return self._start_selected_exercise(exercise)

            if intent == "complete":
                return self.complete_once()

            if intent == "continue":
                return self._base_response(
                    f"好的，继续保持这个节奏。做完以后只说固定口令“{NEXT_GROUP_COMMAND}”。",
                    face="care",
                    motion=self._current_motion()
                )

            if intent == "rest":
                return self.rest()

            if intent in ["end", "decline"]:
                return self.summary()

            if intent == "repeat":
                return self._base_response(
                    f"没关系，我再示范和说明一次。{self._exercise_instruction()} 动作慢一点，不需要追求速度。",
                    face="care",
                    motion=self._current_motion()
                )

            return self._base_response(
                casual_reply(text, state=self.state, exercise_name=self.current_exercise["name"]),
                face="care",
                motion=self._current_motion(),
                use_llm=False
            )

        # 7. 休息状态
        if self.state == "rest":
            if intent in ["continue", "start", "no_risk", "complete"]:
                self.state = "training"
                return self._base_response(
                    f"好的，我们继续。还是慢慢来，做完以后说“{NEXT_GROUP_COMMAND}”。",
                    face="care",
                    motion=self._current_motion()
                )

            if intent in ["end", "decline"]:
                return self.summary()

            if intent == "repeat" and self.current_exercise:
                return self._base_response(
                    f"休息时我再提醒一下：{self._exercise_instruction()} 准备好了说继续。",
                    face="care",
                    motion=self._current_motion()
                )

            if intent == "rest":
                return self._base_response(
                    "好的，我们继续休息，不着急。感觉可以了再说继续。",
                    face="rest",
                    motion="idle"
                )

            return self._base_response(
                casual_reply(text, state=self.state, exercise_name=self.current_exercise["name"] if self.current_exercise else "未开始"),
                face="rest",
                motion="idle",
                use_llm=False
            )

        # 8. 训练后反馈
        if self.state == "feedback":
            if intent in ["continue", "start", "no_risk"]:
                self.count = 0
                self.state = "training"
                return self._base_response(
                    f"好的，如果你感觉还可以，我们再轻轻做一组。还是慢慢来，做完以后说“{NEXT_GROUP_COMMAND}”。",
                    face="care",
                    motion=self._current_motion()
                )

            if intent == "rest":
                return self.summary(user_feedback="感觉有点累，训练后需要休息。")

            return self.summary(user_feedback=text)

        # 9. 安全停止后
        if self.state == "safety_stop":
            if "重新" in text or "开始" in text:
                self.state = "ask_training"
                return self._base_response(
                    "在重新训练前，请先确认已经没有疼痛、头晕、胸闷或明显不舒服。你现在感觉怎么样？",
                    face="care",
                    motion="idle"
                )

            return self._base_response(
                "现在处于安全停止状态。请先休息，不要继续训练。如果不适持续，请联系家属或专业康复人员。",
                face="serious",
                motion="stop",
                show_continue=False
            )

        # 10. 其他无关输入
        return self._base_response(
            "我主要负责康复训练对话、动作指导和训练记录。我们可以继续当前训练，或者结束本次训练。",
            face="care",
            motion="idle"
        )

    def complete_once(self):
        """
        用户说固定口令后调用。
        负责计数、鼓励、判断是否完成整组训练。
        """
        if not self.current_exercise:
            self.state = "ask_training"
            return self._base_response(
                "现在还没有选择训练项目。要不要先做一点轻松的手部训练？",
                face="care",
                motion="idle"
            )

        self.count += 1
        target = self.current_exercise["reps"]

        if self.count >= target:
            self.state = "feedback"
            return self._base_response(
                f"很好，这一组{self.current_exercise['name']}已经完成了。刚才训练过程中有没有疼痛或明显不舒服？也可以说说现在累不累。",
                face="spark",
                motion=self.current_exercise["motion"]
            )

        face = PRAISE_FACES[(self.count - 1) % len(PRAISE_FACES)]
        return self._base_response(
            f"很好，已经完成第{self.count}组，还剩{target - self.count}组。下一组做完后，说“完成”就行。",
            face=face,
            motion=self.current_exercise["motion"]
        )

    def rest(self, reply=None):
        """
        进入休息状态。
        """
        self.state = "rest"
        self.rest_count += 1
        return self._base_response(
            reply or "好的，我们先休息一下。请把手臂放松，慢慢呼吸。感觉可以继续时，再告诉我“继续”。",
            face="rest",
            motion="idle"
        )

    def summary(self, user_feedback=""):
        """
        生成训练总结。
        """
        exercise_name = self.current_exercise["name"] if self.current_exercise else "本次训练"
        target = self.current_exercise["reps"] if self.current_exercise else 0

        self.state = "summary"

        pain_text = "训练中报告过不适，已进行安全停止。" if self.pain_reported else "未报告明显疼痛。"
        feedback_text = f" 你的反馈是：{user_feedback}" if user_feedback else ""

        reply = (
            f"本次训练总结：你完成了{exercise_name} {self.count}次，"
            f"原计划{target}次，中间休息{self.rest_count}次。{pain_text}{feedback_text} "
            f"今天辛苦了，训练后请适当休息。"
        )

        # 总结完成后回到待机
        self.state = "idle"

        return {
            "reply": polish_reply(reply, state="summary", exercise_name=exercise_name),
            "state": "summary",
            "exercise_name": exercise_name,
            "face": "happy",
            "motion": "wave",
            "count": self.count,
            "target": target,
            "show_continue": False
        }
