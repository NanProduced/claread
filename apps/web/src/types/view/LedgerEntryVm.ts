export interface LedgerEntryVm {
  id: string;
  entryType: string;
  points: number;
  bucketType: string;
  balanceAfter: number;
  description: string;
  articleTitle: string | null;
  metadata: Record<string, unknown>;
  taskId: string | null;
  createdAt: string;
}

export interface LedgerListVm {
  items: LedgerEntryVm[];
  cursor: string | null;
  hasMore: boolean;
}
