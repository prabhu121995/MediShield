"use client";

import ConfirmDelete from "@/components/ConfirmDelete";
import { deletePatient } from "@/lib/api";

// Admin only: delete a patient with no claims or pages, then back to the patient list.
export default function DeletePatient({ patientId }: { patientId: string }) {
  return <ConfirmDelete label="Delete patient" question="Delete this patient record? This cannot be undone."
    action={() => deletePatient(patientId)} goTo="/patients" />;
}
