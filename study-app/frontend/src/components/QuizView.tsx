import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, X, Loader2 } from "lucide-react";
import * as api from "../api/client";
import type { QuizContent, QuizQuestion } from "../types";

interface Props {
  contentId: string;
  content: QuizContent;
}

const LETTERS = ["A", "B", "C", "D", "E", "F"];

export function QuizView({ contentId, content }: Props) {
  const queryClient = useQueryClient();
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [submitted, setSubmitted] = useState(false);

  const submit = useMutation({
    mutationFn: (a: Record<string, number>) => api.submitQuiz(contentId, a),
    onSuccess: () => {
      setSubmitted(true);
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
  const passed = scorePct !== null && scorePct >= 70;

  return (
    <div>
      {submitted && attempt && (
        <div className="score-banner">
          <div className={`score-ring ${passed ? "pass" : "fail"}`}>
            {scorePct}%
          </div>
          <div className="score-info">
            <div className="score-label">Your Score</div>
            <div className="score-detail">
              {attempt.correct_count}/{attempt.total_count} correct
            </div>
            <div className="score-msg">
              {passed
                ? "Nice work! You've got a solid grasp of this. 🎉"
                : "Keep studying — you're getting there. 💪"}
            </div>
          </div>
        </div>
      )}

      {content.questions.map((q: QuizQuestion, qi: number) => {
        const picked = answers[q.id];
        const correctIdx = q.answer_idx;
        return (
          <div className="quiz-q" key={q.id}>
            <div className="prompt">
              <span className="q-num">{qi + 1}.</span>
              <span>{q.prompt}</span>
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
                  disabled={submitted}
                >
                  <span className="opt-badge">{LETTERS[oi]}</span>
                  <span>{opt}</span>
                </button>
              );
            })}
            {submitted && (
              <div
                className={`quiz-explanation ${
                  picked === correctIdx ? "correct-exp" : "wrong-exp"
                }`}
              >
                {picked === correctIdx ? (
                  <Check size={16} className="exp-icon ok" />
                ) : (
                  <X size={16} className="exp-icon bad" />
                )}
                <span>
                  {picked === correctIdx ? "Correct. " : "Not quite. "}
                  {q.explanation}
                </span>
              </div>
            )}
          </div>
        );
      })}

      {!submitted && (
        <button
          className="primary"
          style={{ marginTop: 8 }}
          disabled={Object.keys(answers).length < content.questions.length}
          onClick={() => submit.mutate(answers)}
        >
          {submit.isPending ? (
            <>
              <Loader2 size={16} className="spinner" />
              Submitting…
            </>
          ) : (
            <>
              Submit (
              {Object.keys(answers).length}/{content.questions.length} answered)
            </>
          )}
        </button>
      )}
      {submit.isError && (
        <div className="error">
          Submit failed: {(submit.error as Error).message}
        </div>
      )}
    </div>
  );
}
