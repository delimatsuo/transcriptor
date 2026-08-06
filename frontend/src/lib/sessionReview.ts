import type { RecentInterview, SessionReview } from "@/types/ws";

export function canOpenRecentInterview(interview: RecentInterview): boolean {
  return (
    interview.session_status === "completed" &&
    interview.review_status !== "corrupt"
  );
}

export function recentInterviewStatusLabel(interview: RecentInterview): string {
  switch (interview.review_status) {
    case "available":
      return "Disponível para abrir";
    case "ready":
      return "Pronta para revisão";
    case "summary_unavailable":
      return "Relatório indisponível";
    case "transcript_unavailable":
      return "Transcrição indisponível";
    case "incomplete":
      return "Transcrição incompleta";
    case "active":
      return "Sessão ainda ativa";
    case "corrupt":
      return "Registro inválido";
  }
}

export function reviewWarning(review: SessionReview): string | null {
  switch (review.review_status) {
    case "available":
      return null;
    case "ready":
      return null;
    case "summary_unavailable":
      return (
        "Relatório indisponível: os dados de contexto necessários para " +
        "regenerá-lo não foram persistidos. A transcrição continua disponível."
      );
    case "transcript_unavailable":
      return "Transcrição indisponível: esta entrevista não pode ser revisada com segurança.";
    case "incomplete":
      return "Transcrição incompleta: esta sessão não pode ser apresentada como concluída.";
    case "active":
      return "Esta sessão ainda está marcada como ativa e não pode ser reaberta como concluída.";
    case "corrupt":
      return "O registro persistido desta entrevista é inválido.";
  }
}
