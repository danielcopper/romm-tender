/**
 * User-facing confirmation copy for replacing or deleting BIOS files.
 */

/** Copy for a destructive or replacing BIOS confirmation dialog. */
export interface BiosConfirmCopy {
  title: string;
  description: string;
  confirmLabel: string;
}

export function replaceBiosConfirmCopy(
  fileName: string,
  place: string,
  backupRoot: string | undefined,
): BiosConfirmCopy {
  return {
    title: `Replace ${fileName}?`,
    description: `The file at ${place} goes to ${backupRoot ?? "the backup folder"}, and Tender's copy is linked in its place.`,
    confirmLabel: "Replace",
  };
}

export function deleteBiosFileConfirmCopy(name: string): BiosConfirmCopy {
  return {
    title: `Delete ${name}?`,
    description: "Tender removes it from its firmware store and every link to it. Files you placed yourself stay.",
    confirmLabel: "Delete",
  };
}

export function deletePlatformBiosConfirmCopy(platformName: string): BiosConfirmCopy {
  return {
    title: `Delete Tender's BIOS downloads for ${platformName}?`,
    description: `Tender removes every BIOS file it downloaded for ${platformName} and every link to them. Files you placed yourself stay.`,
    confirmLabel: "Delete",
  };
}
