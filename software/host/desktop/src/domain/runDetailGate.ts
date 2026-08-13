export interface RunDetailTicket {
  runId: string;
  generation: number;
  appliesToDetail: boolean;
}

export class RunDetailGate {
  private selected?: string;
  private generation = 0;

  get selectedRunId(): string | undefined {
    return this.selected;
  }

  select(runId: string): void {
    if (this.selected !== runId) {
      this.selected = runId;
      this.generation += 1;
    }
  }

  begin(runId: string, select: boolean): RunDetailTicket {
    if (select || this.selected === undefined) this.select(runId);
    const appliesToDetail = this.selected === runId;
    if (appliesToDetail) this.generation += 1;
    return { runId, generation: this.generation, appliesToDetail };
  }

  isCurrent(ticket: RunDetailTicket): boolean {
    return ticket.appliesToDetail &&
      ticket.runId === this.selected &&
      ticket.generation === this.generation;
  }
}
