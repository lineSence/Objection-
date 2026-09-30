import type { PoolModel } from "../api";
import Composer from "./Composer";

export default function NewQuestion(props: { pool: PoolModel[]; onSubmit: (q: string, models: string[]) => Promise<void> }) {
  return (
    <div className="new">
      <div>
        <h1>Что вынести на совет?</h1>
        <p className="muted" style={{ marginTop: 6 }}>
          Модели ответят независимо, председатель сведёт позиции и сохранит разногласия. Ctrl/⌘ + Enter — отправить.
        </p>
      </div>
      <Composer {...props} autoFocus />
    </div>
  );
}
