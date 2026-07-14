# 对话式康复陪练机器人

一个融合规则状态机、生成式 AI、语音交互与动作动画的康复训练陪伴原型。

> 本项目不提供诊断、用药或治疗建议，也不能替代医生与康复治疗师。

## 本地运行

建议使用 Python 3.10 或更高版本：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

复制公开的配置示例，并填写你自己的 OpenAI API Key：

```bash
cp .env.example .env
```

编辑 `.env`：

```dotenv
OPENAI_API_KEY=your_openai_api_key_here
```

然后启动：

```bash
python app.py
```

浏览器访问 <http://127.0.0.1:5030>。如果不配置 API Key，基础训练流程仍可使用本地兜底话术运行。

## API Key 安全

- `.env` 已被 `.gitignore` 排除，不会随正常的 Git 提交上传。
- 仓库只提交不含真实密钥的 `.env.example`。
- API Key 只在后端读取，不会发送到浏览器前端。
- 请勿把 Key 写入 Python、HTML、截图、日志或提交信息中。

如果 Key 曾经被提交或公开过，仅从文件中删除是不够的；请立即在服务商后台撤销该 Key，并创建新 Key。

更完整的设计、架构与使用说明见 [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)。
