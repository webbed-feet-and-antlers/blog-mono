import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import * as api from "../api/client";
import { track } from "../api/track";
import type { QuizContent, QuizQuestion } from "../types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

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
        <Card className="mb-6 flex flex-row items-center gap-5 px-7 py-6">
          <div
            className={`flex size-16 shrink-0 items-center justify-center rounded-full text-lg font-bold text-white ${
              passed ? "bg-ok" : "bg-destructive"
            }`}
          >
            {scorePct}%
          </div>
          <div>
            <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Your Score
            </div>
            <div className="text-2xl font-bold tracking-tight">
              {attempt.correct_count}/{attempt.total_count} correct
            </div>
            <div className="text-sm text-muted-foreground">
              {passed
                ? "Nice work! You've got a solid grasp of this. 🎉"
                : "Keep studying — you're getting there. 💪"}
            </div>
          </div>
        </Card>
      )}

      {content.questions.map((q: QuizQuestion, qi: number) => {
        const picked = answers[q.id];
        const correctIdx = q.answer_idx;
        return (
          <Card key={q.id} className="mb-3.5 gap-0 px-6 py-5">
            <div className="mb-4 flex gap-2 text-[0.98rem] font-semibold">
              <span className="shrink-0 text-primary">{qi + 1}.</span>
              <span>{q.prompt}</span>
            </div>
            {q.options.map((opt, oi) => {
              const isCorrect = submitted && oi === correctIdx;
              const isWrong = submitted && oi === picked && oi !== correctIdx;
              const isSelected = !submitted && oi === picked;
              return (
                <Button
                  key={oi}
                  variant="outline"
                  className={cn(
                    "mb-2 h-auto w-full justify-start gap-3 px-3.5 py-2.5 text-left font-normal text-[0.92rem] whitespace-normal shadow-none last:mb-0",
                    isSelected && "border-primary bg-accent",
                    isCorrect && "border-ok bg-ok-tint",
                    isWrong && "border-destructive bg-danger-tint",
                  )}
                  onClick={() => select(q, oi)}
                  disabled={submitted}
                >
                  <span
                    className={cn(
                      "flex size-[26px] shrink-0 items-center justify-center rounded-md bg-secondary text-xs font-semibold text-muted-foreground",
                      isSelected && "bg-primary text-primary-foreground",
                      isCorrect && "bg-ok text-white",
                      isWrong && "bg-destructive text-white",
                    )}
                  >
                    {LETTERS[oi]}
                  </span>
                  <span>{opt}</span>
                </Button>
              );
            })}
            {submitted && (
              <Alert
                className={cn(
                  "mt-3.5 rounded-md border-0 border-l-[3px] bg-muted",
                  picked === correctIdx ? "border-l-ok" : "border-l-destructive",
                )}
              >
                {picked === correctIdx ? (
                  <Check className="text-ok!" />
                ) : (
                  <X className="text-destructive!" />
                )}
                <AlertDescription className="text-[0.88rem] text-muted-foreground">
                  {picked === correctIdx ? "Correct. " : "Not quite. "}
                  {q.explanation}
                </AlertDescription>
              </Alert>
            )}
          </Card>
        );
      })}

      {!submitted && (
        <Button
          className="mt-2"
          disabled={Object.keys(answers).length < content.questions.length}
          onClick={() => submit.mutate(answers)}
        >
          {submit.isPending ? (
            <>
              <Spinner className="size-4" />
              Submitting…
            </>
          ) : (
            <>
              Submit (
              {Object.keys(answers).length}/{content.questions.length} answered)
            </>
          )}
        </Button>
      )}
      {submit.isError && (
        <Alert variant="destructive" className="mt-3">
          <AlertDescription>
            Submit failed: {(submit.error as Error).message}
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
