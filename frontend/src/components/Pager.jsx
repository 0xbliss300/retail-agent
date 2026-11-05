import Icon from "./Icons.jsx";

export default function Pager({ hasPrev, hasNext, onPrev, onNext }) {
  return (
    <div className="pagination">
      <button className="btn btn--outline btn--sm" type="button" disabled={!hasPrev} onClick={onPrev}>
        <Icon name="chevron-right" size={14} style={{ transform: "rotate(180deg)" }} />
        上一页
      </button>
      <button className="btn btn--primary btn--sm" type="button" disabled={!hasNext} onClick={onNext}>
        下一页
        <Icon name="chevron-right" size={14} />
      </button>
    </div>
  );
}
