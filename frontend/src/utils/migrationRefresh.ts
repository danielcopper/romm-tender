import { useEffect } from "react";
import { refreshMigrationState, logError } from "../api/backend";
import { setMigrationStatus } from "./migrationStore";

export function useMigrationRefresh(appId: number): void {
  useEffect(() => {
    refreshMigrationState()
      .then(({ retrodeck }) => {
        setMigrationStatus(retrodeck);
      })
      .catch((e) => logError(`Failed to refresh migration state: ${e}`));
  }, [appId]);
}
