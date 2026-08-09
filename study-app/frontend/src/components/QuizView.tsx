import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as api from "../api/client";
import type { QuizContent, QuizQuestion } from "../types";

interface Props {
  contentId: string;
  content: QuizContent;
}

export function QuizView({ contentId, content }: Props) {
  const queryClient = useQueryClient();
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [submitted, setSubmitted] = useState(false);

  const submit = useMutation({
    mutationFn: (a: Record<string, number>) =>
      api.submitQuiz(contentId, a),
    onSuccess: () => {
      setSubmitted(true);
      // New attempt affects agent memory downstream.
      queryClient.invalidateQueries({ queryKey: ["memory"] });
    },
  });

  function select(qid: string, idx: number) {
    if (submitted) return;
    setAnswers((prev) => ({ ...prev, [qid]: idx }));
  }

  const attempt = submit.data;
  const scorePct = attempt
    ? Math.round((attempt.correct_count / attempt.total_count) * 100)
    : null;

  return (
    <div>
      {submitted && attempt && (
        <div className="score-banner">
          Score: <strong>{attempt.correct_count}/{attempt.total_count}</strong>{" "}
          ({scorePct}%) — {scorePct !== null && scorePct >= 70
            ? "Nice work! 🎉"
            : "Keep studying 💪"}
        </div>
      )}

      {content.questions.map((q: QuizQuestion, qi: number) => {
        const picked = answers[q.id];
        const correctIdx = q.answer_idx;
        return (
          <div className="quiz-q" key={q.id}>
            <div className="prompt">
              {qi + 1}. {q.prompt}
            </div>
            {q.options.map((opt, oi) => {
              let cls = "quiz-opt";
              if (submitted) {
                if (oi === correctIdx) cls += " correct";
                else if (oi === picked) cls += " wrong";
              } else if (oi === picked) {
                cls += " selected";
              }
              return (
                <button
                  key={oi}
                  className={cls}
                  onClick={() => select(q.id, oi)}
                >
                  {opt}
                </button>
              );
            })}
            {submitted && (
              <div className="quiz-explanation">
                {picked === correctIdx
                  ? "✅ Correct. "
                  : "❌ Incorrect. "}
                {q.explanation}
              </div>
            )}
          </div>
        );
      })}

      {!submitted && (
        <button
          className="primary"
          disabled={Object.keys(answers).length < content.questions.length}
          onClick={() => submit.mutate(answers)}
        >
          {submit.isPending
            ? "Submitting…"
            : `Submit (${Object.keys(answers).length}/${content.questions.length} answered)`}
        </button>
      )}
      {submit.isError && (
        <div className="error">Submit failed: {(submit.error as Error).message}</div>
      )}
    </div>
  );
}
