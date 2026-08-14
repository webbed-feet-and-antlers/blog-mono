import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, X, Loader2 } from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
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

  // --- Timing (behavioral signal for per-concept difficulty) ---------------
  const startedAtRef = useRef(Date.now());
  // First-answer timestamp per question — latency = first pick − quiz render.
  const firstAnswerAtRef = useRef<Record<string, number>>({});
  useEffect(() => {
    startedAtRef.current = Date.now();
    firstAnswerAtRef.current = {};
  }, [contentId]);

  const submit = useMutation({
    mutationFn: (a: Record<string, number>) => {
      const timings: Record<string, number> = {};
      for (const [qid, at] of Object.entries(firstAnswerAtRef.current)) {
        timings[qid] = Math.max(1, Math.round((at - startedAtRef.current) / 1000));
      }
      return api.submitQuiz(contentId, a, {
        duration_secs: Math.max(
          1,
          Math.round((Date.now() - startedAtRef.current) / 1000),
        ),
        question_timings: timings,
      });
    },
    onSuccess: () => {
      setSubmitted(true);
      queryClient.invalidateQueries({ queryKey: ["memory"] });
      queryClient.invalidateQueries({ queryKey: ["learner-profile"] });
    },
  });

  function select(q: QuizQuestion, idx: number) {
    if (submitted) return;
    const qid = q.id;
    const isFirst = firstAnswerAtRef.current[qid] === undefined;
    if (isFirst) {
      firstAnswerAtRef.current[qid] = Date.now();
      track("quiz.answered", {
        question_id: qid,
        concept: q.concept ?? null,
        latency_secs: Math.max(
          1,
          Math.round((Date.now() - startedAtRef.current) / 1000),
        ),
        changed: false,
      });
    } else if (firstAnswerAtRef.current[qid] !== undefined) {
      track("quiz.answered", {
        question_id: qid,
        concept: q.concept ?? null,
        latency_secs: 0,
        changed: true,
      });
    }
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
                  onClick={() => select(q, oi)}
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
