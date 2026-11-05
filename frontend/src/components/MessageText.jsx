function renderInline(text) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

// 确认编号、会话编号和过期时间属于内部标识，界面上由确认卡片呈现，不重复展示。
const INTERNAL_LINE = /^(确认编号|会话编号|有效期至|有效期)[:：]/;
const INTERNAL_ID = /^(cfm|sess|thread)_[0-9a-zA-Z]+[。.]?$/;

/**
 * 把模型返回的轻量 Markdown 渲染成文本行：只处理标题、列表和加粗，
 * 不注入 HTML，避免模型输出被当作标签执行。
 */
export default function MessageText({ text }) {
  const lines = text.replace(/^\n+/, "").split("\n");
  return lines.map((line, index) => {
    const trimmed = line.trim();
    if (!trimmed) return <div className="bubble__gap" key={index} />;
    if (INTERNAL_LINE.test(trimmed) || INTERNAL_ID.test(trimmed)) return null;

    const bullet = /^[-*•]\s+(.*)$/.exec(trimmed);
    if (bullet) {
      return (
        <div className="bubble__bullet" key={index}>
          {renderInline(bullet[1])}
        </div>
      );
    }

    const heading = /^#{1,4}\s+(.*)$/.exec(trimmed);
    if (heading) {
      return (
        <div className="bubble__heading" key={index}>
          {renderInline(heading[1])}
        </div>
      );
    }

    return (
      <div className="bubble__line" key={index}>
        {renderInline(trimmed)}
      </div>
    );
  });
}
