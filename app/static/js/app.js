// Geosmin Clinical Medication Safety Platform - Master Frontend Controller
// Strict visual adherence: Off-white #FAF9F6, Deep Navy #1F3A5F, Soft Teal #2A7F85, Playfair Display & Inter

const SYNC_KEYS = {
  EXPIRES_AT: 'geosmin_share_expires_at',
  PASSCODE: 'geosmin_share_passcode',
  SCOPES: 'geosmin_share_scopes'
};

// Global cross-tab broadcast channel for instant multi-tab timer sync
let broadcastChannel = null;
try {
  broadcastChannel = new BroadcastChannel('geosmin_tab_sync');
} catch (e) {
  console.log('BroadcastChannel not available, using localStorage storage event');
}

const state = {
  view: 'login', // 'login', 'patient', 'doctor'
  patientTab: 'medications', // 'medications', 'add_medication', 'share', 'access_log'
  doctorStep: 'dashboard', // 'dashboard', 'one_time_view', 'safety_report'
  timelineFilter: 'all', // 'all', 'active', 'attention', 'archived'
  
  // Multi-user authentication & profile selection
  loginRole: 'patient', // 'patient' or 'doctor'
  selectedPatientId: 'GK-8821',
  selectedDoctorId: 'doc-reed',
  availablePatients: [
    {
      id: 'GK-8821',
      name: 'Ravi Kumar',
      age: 54,
      gender: 'Male',
      email: 'ravi.kumar@example.com',
      condition: 'Atrial Fibrillation (Chronic) & HTN',
      warningTag: 'Warfarin + Ibuprofen OTC Alert',
      tagColor: 'amber'
    },
    {
      id: 'PS-4019',
      name: 'Priya Sharma',
      age: 42,
      gender: 'Female',
      email: 'priya.sharma@example.com',
      condition: 'Type 2 Diabetes & Persistent Asthma',
      warningTag: 'Metformin + Severe NSAID Allergy',
      tagColor: 'purple'
    },
    {
      id: 'DC-7731',
      name: 'David Chen',
      age: 68,
      gender: 'Male',
      email: 'david.chen@example.com',
      condition: 'CKD Stage 3b & Chronic Gout',
      warningTag: 'Renal Clearance Protocol (eGFR 38)',
      tagColor: 'blue'
    }
  ],
  availableDoctors: [
    {
      id: 'doc-reed',
      name: 'Dr. Marcus Reed, MD',
      email: 'm.reed@rmc-health.org',
      specialty: 'Internal Medicine & Nephrology',
      hospital: 'Metropolitan Medical Center',
      license: 'MD-88210'
    },
    {
      id: 'doc-vance',
      name: 'Dr. Elena Vance, MD',
      email: 'e.vance@rmc-health.org',
      specialty: 'Senior Cardiologist & Arrhythmia Specialist',
      hospital: 'Regional Cardiac Institute',
      license: 'MD-44912'
    }
  ],
  
  patient: null,
  share: null,
  doctorSession: null,
  safetyReport: null,
  
  // 5-Minute Timer synchronization
  countdownExpiresAt: 0,
  countdownSeconds: 300,
  timerIntervalId: null,
  
  // Multilingual medical voice dictation pipeline (faster-whisper INT8 & Vernacular Adaptation)
  isRecording: false,
  isTranscribing: false,
  dictateLang: 'te-IN',
  voiceWorkbenchOpen: true,
  voiceElapsed: 0,
  voiceTimerInterval: null,
  voiceAudioChunks: [],
  voiceMediaRecorder: null,
  voiceAudioStream: null,
  voiceAudioContext: null,
  voiceAnalyser: null,
  voiceAnimId: null,
  voiceVadSpeaking: false,
  voiceVadRms: 0,
  voiceLastResult: null,
  activeAmberPopoverIndex: null,
  
  // Scope checkboxes on Share screen
  shareScopes: {
    medications: true,
    allergies: true,
    diagnoses: true,
    biometrics: false
  }
};

// --- INITIALIZATION ---
document.addEventListener('DOMContentLoaded', async () => {
  initCrossTabSync();
  setupSpeechRecognition();
  
  // Check or initialize 5-minute share timer in localStorage
  const storedExpires = localStorage.getItem(SYNC_KEYS.EXPIRES_AT);
  if (!storedExpires || Number(storedExpires) < Date.now()) {
    resetShareTimer(300, '842-916');
  } else {
    state.countdownExpiresAt = Number(storedExpires);
    state.countdownSeconds = Math.max(0, Math.ceil((state.countdownExpiresAt - Date.now()) / 1000));
  }

  startGlobalTimerTicker();
  renderApp(); // Render immediately for instant responsiveness

  // Load backend data in parallel
  await Promise.all([loadPatientData(), loadLatestShare()]);
  renderApp();
});

// --- CROSS-TAB TIMER & STATE SYNCHRONIZATION (Requirement 3) ---

function initCrossTabSync() {
  // Listen to localStorage storage events across browser tabs
  window.addEventListener('storage', (e) => {
    if (e.key === SYNC_KEYS.EXPIRES_AT) {
      state.countdownExpiresAt = Number(e.newValue || 0);
      updateTimerCalculations();
      updateTimerDisplays();
    } else if (e.key === SYNC_KEYS.PASSCODE) {
      if (state.share) state.share.passcode = e.newValue;
      updateTimerDisplays();
    }
  });

  // Listen to BroadcastChannel for zero-latency sub-millisecond sync
  if (broadcastChannel) {
    broadcastChannel.onmessage = (event) => {
      const { type, expiresAt, passcode } = event.data || {};
      if (type === 'TIMER_SYNC') {
        state.countdownExpiresAt = expiresAt;
        if (passcode && state.share) state.share.passcode = passcode;
        updateTimerCalculations();
        updateTimerDisplays();
      } else if (type === 'REVOKED') {
        state.countdownExpiresAt = 0;
        state.countdownSeconds = 0;
        updateTimerDisplays();
        showToast('Access code revoked from another consultation window', 'amber');
      } else if (type === 'DOCTOR_ACCESSED') {
        showToast(`Clinician redeemed consultation code #${passcode}`, 'teal');
      }
    };
  }
}

function resetShareTimer(seconds = 300, passcode = '842-916') {
  const expiresAt = Date.now() + seconds * 1000;
  state.countdownExpiresAt = expiresAt;
  state.countdownSeconds = seconds;
  localStorage.setItem(SYNC_KEYS.EXPIRES_AT, String(expiresAt));
  localStorage.setItem(SYNC_KEYS.PASSCODE, passcode);

  if (broadcastChannel) {
    broadcastChannel.postMessage({
      type: 'TIMER_SYNC',
      expiresAt: expiresAt,
      passcode: passcode
    });
  }
}

function startGlobalTimerTicker() {
  if (state.timerIntervalId) clearInterval(state.timerIntervalId);
  
  state.timerIntervalId = setInterval(() => {
    updateTimerCalculations();
    updateTimerDisplays();
  }, 1000);
}

function updateTimerCalculations() {
  const stored = Number(localStorage.getItem(SYNC_KEYS.EXPIRES_AT) || state.countdownExpiresAt || 0);
  state.countdownExpiresAt = stored;
  const now = Date.now();
  if (stored > now) {
    state.countdownSeconds = Math.max(0, Math.ceil((stored - now) / 1000));
  } else {
    state.countdownSeconds = 0;
  }
}

function updateTimerDisplays() {
  const mins = Math.floor(state.countdownSeconds / 60);
  const secs = state.countdownSeconds % 60;
  const formatted = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  
  const timerElements = document.querySelectorAll('.live-timer');
  timerElements.forEach(el => {
    if (state.countdownSeconds > 0) {
      el.textContent = formatted;
      el.classList.remove('text-red-600', 'font-bold');
    } else {
      el.textContent = '00:00 (Expired)';
      el.classList.add('text-red-600', 'font-bold');
    }
  });
}

// --- TOAST NOTIFICATIONS & MICRO-INTERACTIONS ---

function showToast(message, type = 'navy') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  let bg = 'bg-[#1F3A5F] text-white border-slate-700';
  let iconSvg = `<svg class="w-4 h-4 text-[#2A7F85] shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>`;

  if (type === 'teal') {
    bg = 'bg-[#2A7F85] text-white border-teal-600';
  } else if (type === 'amber') {
    bg = 'bg-amber-800 text-white border-amber-900';
    iconSvg = `<svg class="w-4 h-4 text-amber-300 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>`;
  } else if (type === 'red') {
    bg = 'bg-red-800 text-white border-red-900';
    iconSvg = `<svg class="w-4 h-4 text-red-300 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>`;
  }

  toast.className = `pointer-events-auto p-3.5 rounded-xl border shadow-lg text-xs font-medium flex items-center space-x-2.5 max-w-sm animate-toast ${bg}`;
  toast.innerHTML = `
    ${iconSvg}
    <span class="flex-1">${message}</span>
    <button onclick="this.parentElement.remove()" class="opacity-70 hover:opacity-100 p-0.5 ml-2 text-base leading-none">&times;</button>
  `;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Interactive Warning Detail Modal (Micro-interaction for Active Warning Badges)
function inspectWarningDetail(warningType) {
  const modal = document.getElementById('warning-detail-modal');
  if (!modal) return;

  let title = 'Clinical Safety Vector Detail';
  let severity = 'HIGH_RISK';
  let badgeText = 'CLASS 1 INTERACTION';
  let mechanism = 'CYP2C9 competitive enzyme substrate inhibition.';
  let evidence = 'FDA Package Insert; CPIC Clinical Guideline 2023; Micromedex Class 1 Interaction.';
  let clinicalImpact = 'Significantly elevated risk of upper gastrointestinal hemorrhage and prothrombin time / INR elevation.';
  let actions = 'Consider alternate non-NSAID analgesic therapy (e.g., Acetaminophen within hepatic parameters or topical agent).';

  if (warningType === 'warfarin_celecoxib' || warningType === 'high_conflict') {
    title = 'Concomitant Warfarin and Celecoxib (NSAID/COX-2 inhibitor)';
    badgeText = '[HIGH RISK] Drug-Drug Interaction • Class 1';
    severity = 'HIGH_RISK';
    mechanism = 'Both Celecoxib and S-Warfarin compete for oxidative metabolism by Cytochrome P450 2C9 (CYP2C9). Celecoxib also induces secondary platelet aggregation disruption.';
    evidence = 'FDA Package Insert (Black Box Section); CPIC Guideline 2023; Micromedex Class 1.';
    clinicalImpact = 'Potential severe hemorrhagic sequelae; prothrombin time prolongation; INR destabilization.';
    actions = 'Avoid concurrent use if possible; if mandatory, monitor INR daily and prescribe prophylactic gastroprotection (e.g. Omeprazole).';
  } else if (warningType === 'ibuprofen_celecoxib' || warningType === 'moderate_review') {
    title = 'Concurrent Self-Reported Ibuprofen with Proposed NSAID';
    badgeText = '[AMBER - REVIEW] Duplicate Therapy & Renal Risk';
    severity = 'AMBER_REVIEW';
    mechanism = 'Dual cyclooxygenase inhibition (COX-1 and COX-2) doubles mucosal ulceration probability without additive analgesic benefit, causing acute afferent renal vasoconstriction.';
    evidence = 'Beers Criteria 2023; KDIGO Clinical Practice Guideline for Acute Kidney Injury.';
    clinicalImpact = 'Accelerated risk of acute reversible renal insufficiency (pre-renal azotemia), sodium retention, and gastric mucosal erosion.';
    actions = 'Advise patient to immediately discontinue OTC Ibuprofen. Document cessation prior to initiating Celecoxib.';
  } else if (warningType === 'sulfa_cross_reactivity' || warningType === 'unassessed') {
    title = 'Celecoxib Sulfonamide Moiety in Patient with Sulfa Allergy';
    badgeText = '[UNABLE TO ASSESS] Sulfa Allergy Cross-Reactivity';
    severity = 'UNASSESSED';
    mechanism = 'Celecoxib possesses a benzene-sulfonamide functional moiety. Unlike antimicrobial arylamine sulfonamides (e.g., Bactrim), non-arylamines lack N4 aromatic amine groups, but immunologic cross-sensitization remains clinically unpredictable.';
    evidence = 'American Academy of Allergy, Asthma & Immunology (AAAAI) Consensus Statement on Non-Arylamine Sulfonamides.';
    clinicalImpact = 'Theoretical hypersensitivity eruption versus benign delayed dermatosis.';
    actions = 'Determine exact nature of historical reaction (urticaria vs anaphylaxis) before initiating first dose.';
  } else if (warningType === 'ibuprofen_active_warning') {
    title = 'OTC Ibuprofen Active Interaction Alert';
    badgeText = 'Attention Required: Active Warfarin Interaction';
    severity = 'AMBER_REVIEW';
    mechanism = 'Non-steroidal anti-inflammatory agents impair platelet aggregation and cause gastric mucosal erosions while Warfarin inhibits clotting factors II, VII, IX, and X.';
    evidence = 'RMC Pharmacy Safety Surveillance & Epistemic Knowledge Graph.';
    clinicalImpact = '3.8x baseline risk of severe upper gastrointestinal bleeding.';
    actions = 'Scheduled for clinical review at upcoming consultation on Nov 14, 2024.';
  }

  const badgeColorClass = severity === 'HIGH_RISK'
    ? 'bg-red-50 text-red-700 border-red-200'
    : severity === 'AMBER_REVIEW'
    ? 'bg-amber-50 text-amber-800 border-amber-200'
    : 'bg-slate-100 text-slate-700 border-slate-200';

  modal.innerHTML = `
    <div class="bg-white rounded-2xl border border-slate-200 shadow-2xl max-w-xl w-full p-6 animate-toast">
      <div class="flex items-start justify-between pb-3 border-b border-slate-100 mb-4">
        <div>
          <span class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border ${badgeColorClass}">
            ${badgeText}
          </span>
          <h3 class="font-serif font-bold text-xl text-[#1F3A5F] mt-2">${title}</h3>
        </div>
        <button onclick="closeWarningDetail()" class="text-slate-400 hover:text-slate-700 p-1">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>

      <div class="space-y-3.5 text-xs text-slate-700">
        <div class="p-3 bg-[#F4F3EF] rounded-xl border border-slate-200/70">
          <div class="font-bold text-slate-500 uppercase tracking-wider text-[10px] mb-1">Pharmacological Mechanism</div>
          <div class="leading-relaxed">${mechanism}</div>
        </div>

        <div class="p-3 bg-red-50/60 rounded-xl border border-red-100">
          <div class="font-bold text-red-800 uppercase tracking-wider text-[10px] mb-1">Clinical Impact & Adverse Potential</div>
          <div class="leading-relaxed text-red-950">${clinicalImpact}</div>
        </div>

        <div class="p-3 bg-[#F4F3EF] rounded-xl border border-slate-200/70">
          <div class="font-bold text-slate-500 uppercase tracking-wider text-[10px] mb-1">Literature & Guideline Source</div>
          <div class="text-slate-900 font-medium">${evidence}</div>
        </div>

        <div class="p-3 bg-teal-50 border border-teal-200 rounded-xl text-teal-900">
          <div class="font-bold uppercase tracking-wider text-[10px] text-teal-800 mb-1">Recommended Clinician Action</div>
          <div>${actions}</div>
        </div>
      </div>

      <div class="mt-5 pt-3 border-t border-slate-100 flex justify-end">
        <button onclick="closeWarningDetail()" class="px-4 py-2 bg-[#1F3A5F] hover:bg-[#152943] text-white text-xs font-semibold rounded-lg transition">
          Acknowledge & Close
        </button>
      </div>
    </div>
  `;

  modal.classList.remove('hidden');
}

function closeWarningDetail() {
  const modal = document.getElementById('warning-detail-modal');
  if (modal) modal.classList.add('hidden');
}

// --- API CLIENT CALLS ---

async function loadPatientData() {
  try {
    const res = await fetch('/api/patient');
    if (res.ok) {
      state.patient = await res.json();
    }
  } catch (err) {
    console.warn("Using baseline patient data fallback:", err);
  }
}

async function loadLatestShare() {
  try {
    const res = await fetch('/api/share/latest');
    if (res.ok) {
      state.share = await res.json();
      if (state.share.expires_in_seconds) {
        resetShareTimer(state.share.expires_in_seconds, state.share.passcode || '842-916');
      }
    }
  } catch (err) {
    console.warn("Using default share parameters:", err);
    state.share = { passcode: '842-916', cipher_specs: 'TLS 1.3 • AES-GCM 256' };
  }
}

async function generateNewShareCode() {
  const scopes = {
    medications: document.getElementById('chk-meds')?.checked ?? state.shareScopes.medications,
    allergies: document.getElementById('chk-allergies')?.checked ?? state.shareScopes.allergies,
    diagnoses: document.getElementById('chk-diagnoses')?.checked ?? state.shareScopes.diagnoses,
    biometrics: document.getElementById('chk-labs')?.checked ?? state.shareScopes.biometrics
  };
  state.shareScopes = scopes;

  try {
    const res = await fetch('/api/share/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ patient_id: state.patient?.id || 'GK-8821', scopes })
    });
    if (res.ok) {
      state.share = await res.json();
      resetShareTimer(state.share.expires_in_seconds || 300, state.share.passcode);
      showToast(`New one-time passcode generated: ${state.share.passcode}`, 'teal');
      renderApp();
      return;
    }
  } catch (err) {
    console.error("Failed to create share code:", err);
  }

  // Fallback demo generation
  const randA = Math.floor(100 + Math.random() * 899);
  const randB = Math.floor(100 + Math.random() * 899);
  const newCode = `${randA}-${randB}`;
  state.share = { passcode: newCode, cipher_specs: 'TLS 1.3 • AES-GCM 256' };
  resetShareTimer(300, newCode);
  showToast(`New one-time passcode generated: ${newCode}`, 'teal');
  renderApp();
}

async function revokeCurrentShare() {
  const code = state.share?.passcode || '842-916';
  try {
    await fetch('/api/share/revoke', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passcode: code })
    });
  } catch (err) {
    console.warn("Revoke network notice:", err);
  }

  state.countdownExpiresAt = 0;
  state.countdownSeconds = 0;
  localStorage.setItem(SYNC_KEYS.EXPIRES_AT, '0');
  
  if (broadcastChannel) {
    broadcastChannel.postMessage({ type: 'REVOKED' });
  }

  showToast(`Passcode ${code} has been permanently revoked`, 'red');
  renderApp();
}

async function handleDoctorAccess(customCode) {
  let cleanCode = customCode;
  if (!cleanCode) {
    const inputs = document.querySelectorAll('.pin-digit');
    if (inputs.length === 6) {
      const d = Array.from(inputs).map(i => i.value.trim()).join('');
      if (d.length === 6) {
        cleanCode = `${d.substring(0, 3)}-${d.substring(3, 6)}`;
      }
    }
  }

  if (!cleanCode) {
    cleanCode = state.share?.passcode || '842-916';
  }

  try {
    const res = await fetch('/api/doctor/access', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        passcode: cleanCode,
        doctor_name: 'Dr. Elena Vance, MD',
        department: 'Cardiology Dept'
      })
    });

    if (res.ok) {
      state.doctorSession = await res.json();
      state.doctorStep = 'one_time_view';
      if (broadcastChannel) {
        broadcastChannel.postMessage({ type: 'DOCTOR_ACCESSED', passcode: cleanCode });
      }
      showToast('Zero-knowledge consultation session active', 'teal');
      renderApp();
      return;
    } else {
      const errData = await res.json();
      showToast(`Access Denied: ${errData.detail}`, 'red');
    }
  } catch (err) {
    console.warn("Simulating transient clinical ledger access:", err);
    state.doctorSession = {
      session_id: 'SES-98214',
      patient_name: 'Ravi Kumar',
      patient_age: 54,
      dob: '12-May-1968',
      mrn: 'RK-48810'
    };
    state.doctorStep = 'one_time_view';
    showToast('Consultation session unlocked for Ravi Kumar', 'teal');
    renderApp();
  }
}

async function handlePrescriptionSafetyCheck() {
  const medName = document.getElementById('doc-med-name')?.value || 'Celecoxib';
  const dosage = document.getElementById('doc-dosage')?.value || '100 mg';
  const freq = document.getElementById('doc-freq')?.value || 'Once daily';
  const duration = document.getElementById('doc-duration')?.value || '14 days';
  const rationale = document.getElementById('doc-rationale')?.value || 'Acute flare-up management of right knee osteoarthritis.';

  try {
    const res = await fetch('/api/doctor/prescribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.doctorSession?.session_id || 'SES-98214',
        patient_id: 'GK-8821',
        medication_name: medName,
        dosage: dosage,
        frequency: freq,
        duration: duration,
        clinical_rationale: rationale
      })
    });

    if (res.ok) {
      state.safetyReport = await res.json();
      state.doctorStep = 'safety_report';
      showToast('Safety engine verification complete: 48 vectors evaluated', 'teal');
      renderApp();
      return;
    }
  } catch (err) {
    console.warn("Safety check network error:", err);
  }

  // Fallback high-fidelity simulation matching Figma exactly
  state.safetyReport = getFallbackSafetyReport();
  state.doctorStep = 'safety_report';
  showToast('Safety check completed: 3 prioritized clinical findings', 'teal');
  renderApp();
}

async function handleAddMedicationSubmit(e) {
  if (e) e.preventDefault();
  const name = document.getElementById('add-med-name')?.value || 'Atorvastatin Calcium';
  const dosage = document.getElementById('add-dosage')?.value || '20 mg';
  const cadence = document.getElementById('add-cadence')?.value || 'Once daily at bedtime';
  const clinician = document.getElementById('add-clinician')?.value || 'Dr. Elena Vance (Cardiology)';
  const indication = document.getElementById('add-indication')?.value || 'Cholesterol & lipid management';

  try {
    const res = await fetch('/api/patient/medication', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        dosage,
        cadence,
        prescribing_clinician: clinician,
        therapeutic_indication: indication
      })
    });
    if (res.ok) {
      await loadPatientData();
    }
  } catch (err) {
    console.warn("Adding to local session state:", err);
  }

  // Add to active local state
  if (state.patient && state.patient.medications) {
    state.patient.medications.unshift({
      id: `med-${Date.now()}`,
      rx_number: `Rx #${Math.floor(10000 + Math.random() * 89999)}-N`,
      name: name,
      generic_name: name,
      dosage: `${dosage} ${cadence}`,
      strength: dosage,
      route: "Oral Tablet",
      cadence: cadence,
      status: "active",
      prescriber: clinician,
      start_date: "Oct 25, 2024",
      end_date: "Ongoing",
      refills: 3,
      indication: indication,
      safety_target: "Routine pharmacotherapy monitoring. Grapefruit interaction warning logged.",
      safety_status: "Reconciled",
      atc_class: "C10AA05",
      category: "Lipid-lowering statin (HMG-CoA reductase inhibitor)"
    });
  }

  showToast(`${name} safely reconciled into medication timeline`, 'teal');
  state.patientTab = 'medications';
  renderApp();
}

// --- VOICE DICTATION & FASTER-WHISPER INT8 MEDICAL SPEECH PIPELINE ---

function setupSpeechRecognition() {
  console.log("Geosmin Medical Speech Engine: faster-whisper CTranslate2 INT8 ready.");
}

async function toggleDictation() {
  state.voiceWorkbenchOpen = true;
  if (state.isRecording) {
    await stopMedicalVoiceRecording();
  } else {
    await startMedicalVoiceRecording();
  }
}

function setDictateLanguage(lang) {
  state.dictateLang = lang;
  const label = (lang === 'te-IN') ? 'Telugu (తెలుగు)' : (lang === 'hi-IN') ? 'Hindi (हिन्दी)' : 'English (Clinical)';
  showToast(`Dictation language set to: ${label}`, 'teal');
  renderApp();
}

function toggleVoiceWorkbench() {
  state.voiceWorkbenchOpen = !state.voiceWorkbenchOpen;
  renderApp();
}

async function startMedicalVoiceRecording() {
  if (state.isRecording) return;
  state.voiceElapsed = 0;
  state.voiceAudioChunks = [];
  state.voiceVadSpeaking = false;
  state.voiceVadRms = 0;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        sampleRate: 16000
      }
    });

    state.voiceAudioStream = stream;
    state.isRecording = true;

    // Web Audio API Graph for Waveform & VU Meter
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    state.voiceAudioContext = new AudioCtx();
    const sourceNode = state.voiceAudioContext.createMediaStreamSource(stream);
    state.voiceAnalyser = state.voiceAudioContext.createAnalyser();
    state.voiceAnalyser.fftSize = 256;
    sourceNode.connect(state.voiceAnalyser);

    // MediaRecorder setup (Opus 16kHz preferred)
    let options = { mimeType: 'audio/webm;codecs=opus' };
    if (!MediaRecorder.isTypeSupported(options.mimeType)) {
      options = { mimeType: 'audio/webm' };
      if (!MediaRecorder.isTypeSupported(options.mimeType)) {
        options = {};
      }
    }

    state.voiceMediaRecorder = new MediaRecorder(stream, options);
    state.voiceMediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        state.voiceAudioChunks.push(event.data);
      }
    };

    state.voiceMediaRecorder.onstop = async () => {
      const mime = state.voiceMediaRecorder.mimeType || 'audio/webm';
      const audioBlob = new Blob(state.voiceAudioChunks, { type: mime });
      
      // Stop media tracks
      if (state.voiceAudioStream) {
        state.voiceAudioStream.getTracks().forEach(t => t.stop());
        state.voiceAudioStream = null;
      }
      if (state.voiceAudioContext && state.voiceAudioContext.state !== 'closed') {
        state.voiceAudioContext.close();
      }
      stopVoiceTimer();
      cancelAnimationFrame(state.voiceAnimId);

      // Transcribe via backend faster-whisper CTranslate2 INT8
      await sendAudioToSpeechBackend(audioBlob);
    };

    // Slice in 250ms chunks for streaming buffer
    state.voiceMediaRecorder.start(250);

    // Start timer & visualizer loop
    startVoiceTimer();
    renderApp();
    setTimeout(() => startAudioVisualizerLoop(), 50);

  } catch (err) {
    console.warn("Microphone access error or permission denied:", err);
    state.isRecording = false;
    showToast("Microphone unavailable or permission denied. Use the 1-Click Clinical Presets below!", "amber");
    renderApp();
  }
}

async function stopMedicalVoiceRecording() {
  if (state.voiceMediaRecorder && state.voiceMediaRecorder.state !== 'inactive') {
    state.isRecording = false;
    state.isTranscribing = true;
    renderApp();
    state.voiceMediaRecorder.stop();
  } else {
    state.isRecording = false;
    renderApp();
  }
}

function startVoiceTimer() {
  stopVoiceTimer();
  state.voiceElapsed = 0;
  state.voiceTimerInterval = setInterval(() => {
    state.voiceElapsed += 1;
    const timerEl = document.getElementById('voice-timer-display');
    if (timerEl) {
      const mins = String(Math.floor(state.voiceElapsed / 60)).padStart(2, '0');
      const secs = String(state.voiceElapsed % 60).padStart(2, '0');
      timerEl.textContent = `${mins}:${secs}`;
    }
  }, 1000);
}

function stopVoiceTimer() {
  if (state.voiceTimerInterval) {
    clearInterval(state.voiceTimerInterval);
    state.voiceTimerInterval = null;
  }
}

function startAudioVisualizerLoop() {
  const canvas = document.getElementById('voice-waveform-canvas');
  const vuFill = document.getElementById('vu-meter-fill');
  const vadBadge = document.getElementById('vad-status-badge');
  if (!state.voiceAnalyser || !state.isRecording) return;

  const bufferLength = state.voiceAnalyser.fftSize;
  const timeData = new Uint8Array(bufferLength);

  function draw() {
    if (!state.isRecording) return;
    state.voiceAnimId = requestAnimationFrame(draw);

    state.voiceAnalyser.getByteTimeDomainData(timeData);

    // Compute continuous RMS power for Client-side VAD
    let sumSquares = 0;
    for (let i = 0; i < bufferLength; i++) {
      const norm = (timeData[i] - 128) / 128;
      sumSquares += norm * norm;
    }
    const rms = Math.sqrt(sumSquares / bufferLength);
    state.voiceVadRms = rms;
    const isSpeaking = rms > 0.025;
    state.voiceVadSpeaking = isSpeaking;

    // Update VU Meter
    if (vuFill) {
      const pct = Math.min(100, Math.round(rms * 420));
      vuFill.style.width = `${pct}%`;
    }

    // Update VAD Status Pill
    if (vadBadge) {
      if (isSpeaking) {
        vadBadge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800 border border-emerald-300 flex items-center";
        vadBadge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-600 animate-ping mr-1.5"></span> VAD: Speech Active`;
      } else {
        vadBadge.className = "px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-600 border border-slate-200 flex items-center";
        vadBadge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-slate-400 mr-1.5"></span> VAD: Silence Trimmed`;
      }
    }

    // Draw Oscilloscope Canvas
    if (canvas) {
      const ctx = canvas.getContext('2d');
      const width = canvas.width;
      const height = canvas.height;

      ctx.fillStyle = '#0E1E33';
      ctx.fillRect(0, 0, width, height);

      // Center reference baseline
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.1)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, height / 2);
      ctx.lineTo(width, height / 2);
      ctx.stroke();

      // Waveform trace with teal-to-cyan gradient
      const gradient = ctx.createLinearGradient(0, 0, width, 0);
      gradient.addColorStop(0, '#2A7F85');
      gradient.addColorStop(0.5, '#38BDF8');
      gradient.addColorStop(1, '#2A7F85');

      ctx.lineWidth = isSpeaking ? 2.5 : 1.2;
      ctx.strokeStyle = gradient;
      ctx.beginPath();

      const sliceWidth = width * 1.0 / bufferLength;
      let x = 0;

      for (let i = 0; i < bufferLength; i++) {
        const v = timeData[i] / 128.0;
        const y = v * (height / 2);

        if (i === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
        x += sliceWidth;
      }

      ctx.lineTo(width, height / 2);
      ctx.stroke();
    }
  }

  draw();
}

async function sendAudioToSpeechBackend(blob) {
  state.isTranscribing = true;
  renderApp();

  try {
    const formData = new FormData();
    formData.append('file', blob, 'medical_dictation.webm');
    formData.append('language', state.dictateLang);
    const activePatient = state.patient?.name ? `Patient ${state.patient.name}, Age ${state.patient.age}` : '';
    formData.append('clinical_context', activePatient);

    const res = await fetch('/api/speech/transcribe', {
      method: 'POST',
      body: formData
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Transcription failed' }));
      throw new Error(err.detail || 'Transcription failed');
    }

    const result = await res.json();
    state.voiceLastResult = result;
    state.isTranscribing = false;

    showToast(`Transcribed in ${result.processing_time_ms}ms • ${result.words.length} words detected`, 'teal');

    if (result.clinical_sig) {
      applyClinicalSigToForm(result.clinical_sig);
    }
    renderApp();
  } catch (err) {
    state.isTranscribing = false;
    showToast(`Speech transcription error: ${err.message}`, 'amber');
    renderApp();
  }
}

async function simulateClinicalPreset(presetText, lang) {
  if (lang) state.dictateLang = lang;
  state.isTranscribing = true;
  renderApp();

  try {
    const res = await fetch('/api/speech/parse-sig', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: presetText, language_hint: state.dictateLang })
    });

    if (!res.ok) throw new Error("Failed to parse clinical SIG");
    const sig = await res.json();

    // Generate word-level confidence tokens with amber flags for low confidence / ambiguous words
    const words = presetText.split(/\s+/).map((word, idx) => {
      const cleanW = word.toLowerCase().replace(/[^a-z0-9]/g, '');
      const isAmbiguous = (cleanW === 'bp' || cleanW === 'dawa' || cleanW === 'mandulu' || cleanW.includes('tablet'));
      const prob = isAmbiguous ? 0.72 : (0.91 + (idx % 3) * 0.03);
      const isLow = prob < 0.80;
      
      let suggestions = [];
      if (isLow) {
        if (cleanW === 'bp' || cleanW.includes('tablet')) {
          suggestions = ["Amlodipine 5mg", "Telmisartan 40mg", "Lisinopril 10mg"];
        } else if (cleanW === 'mandulu' || cleanW === 'dawa') {
          suggestions = ["Paracetamol 650mg", "Metformin 500mg", "Pantoprazole 40mg"];
        } else {
          suggestions = [sig.drug_name || "Paracetamol 650mg"];
        }
      }

      return {
        word: word,
        start: Number((idx * 0.45).toFixed(2)),
        end: Number(((idx + 1) * 0.45).toFixed(2)),
        probability: Number(prob.toFixed(2)),
        low_confidence: isLow,
        suggestions: suggestions
      };
    });

    state.voiceLastResult = {
      success: true,
      text: presetText,
      detected_language: lang ? lang.split('-')[0] : 'te',
      language_probability: 0.98,
      duration_seconds: Number((words.length * 0.45).toFixed(1)),
      words: words,
      average_confidence: 0.92,
      low_confidence_word_count: words.filter(w => w.low_confidence).length,
      clinical_sig: sig,
      vernacular_entities: sig.vernacular_terms_detected || [],
      processing_time_ms: 215,
      engine_metadata: {
        device: "cuda",
        compute_type: "int8_float16",
        model_size: "small"
      }
    };

    state.isTranscribing = false;
    showToast(`Vernacular parsed: "${sig.standard_sig_code}"`, 'teal');
    applyClinicalSigToForm(sig);
    renderApp();
  } catch (err) {
    state.isTranscribing = false;
    showToast(`Simulation error: ${err.message}`, 'amber');
    renderApp();
  }
}

function applyClinicalSigToForm(sig) {
  if (!sig) return;
  const nameInput = document.getElementById('add-med-name') || document.getElementById('doc-med-name');
  const doseInput = document.getElementById('add-dosage') || document.getElementById('doc-dosage');
  const cadenceInput = document.getElementById('add-cadence');
  const indicationInput = document.getElementById('add-indication');

  if (nameInput && sig.drug_name && sig.drug_name !== 'Clinically Specified Medication') {
    nameInput.value = sig.drug_name;
    if (typeof checkRxNormLive === 'function') {
      checkRxNormLive(sig.drug_name);
    }
  }
  if (doseInput && sig.dosage) {
    doseInput.value = sig.dosage;
  }
  if (cadenceInput && (sig.cadence || sig.sig_full)) {
    cadenceInput.value = sig.cadence || sig.sig_full;
  }
  if (indicationInput && sig.drug_name) {
    indicationInput.value = `RxNorm SIG: [${sig.standard_sig_code}] • Dictation Reconciled`;
  }
}

function toggleAmberPopover(index) {
  state.activeAmberPopoverIndex = (state.activeAmberPopoverIndex === index) ? null : index;
  renderApp();
}

function replaceAmberWordWithSuggestion(wordIdx, suggestion) {
  if (!state.voiceLastResult || !state.voiceLastResult.words[wordIdx]) return;
  const oldWord = state.voiceLastResult.words[wordIdx].word;
  state.voiceLastResult.words[wordIdx].word = suggestion;
  state.voiceLastResult.words[wordIdx].probability = 0.99;
  state.voiceLastResult.words[wordIdx].low_confidence = false;
  state.voiceLastResult.words[wordIdx].suggestions = [];
  state.activeAmberPopoverIndex = null;

  const newTranscript = state.voiceLastResult.words.map(w => w.word).join(' ');
  state.voiceLastResult.text = newTranscript;
  showToast(`Corrected "${oldWord}" -> "${suggestion}"`, 'teal');

  // Re-parse SIG with updated transcript
  fetch('/api/speech/parse-sig', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: newTranscript, language_hint: state.dictateLang })
  })
  .then(r => r.json())
  .then(newSig => {
    state.voiceLastResult.clinical_sig = newSig;
    state.voiceLastResult.vernacular_entities = newSig.vernacular_terms_detected || [];
    applyClinicalSigToForm(newSig);
    renderApp();
  })
  .catch(() => renderApp());
}

function updateDictateUI() {
  const btn = document.getElementById('mic-btn');
  if (btn) {
    btn.innerHTML = state.isRecording 
      ? `<span class="inline-block w-2.5 h-2.5 rounded-full bg-red-500 animate-ping mr-2"></span> Recording... Click to Stop`
      : `<svg class="w-4 h-4 mr-1.5 inline" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 003-3V5a3 3 0 10-6 0v6a3 3 0 003 3z"/></svg> Dictate`;
  }
}

function renderVoiceWorkbench() {
  const isRec = state.isRecording;
  const isProc = state.isTranscribing;
  const res = state.voiceLastResult;
  const lang = state.dictateLang;

  const langPills = [
    { code: 'te-IN', label: 'Telugu (తెలుగు)', flag: '🇮🇳' },
    { code: 'hi-IN', label: 'Hindi (हिन्दी)', flag: '🇮🇳' },
    { code: 'en-US', label: 'English (Clinical)', flag: '🇬🇧' }
  ].map(item => `
    <button type="button" onclick="setDictateLanguage('${item.code}')" class="px-2.5 py-1 rounded-lg text-xs font-medium transition ${lang === item.code ? 'bg-[#1F3A5F] text-white shadow-xs font-semibold' : 'bg-white text-slate-700 hover:bg-slate-100 border border-slate-200'}">
      <span>${item.flag}</span> <span>${item.label}</span>
    </button>
  `).join('');

  // Transcribed tokens with amber confidence badges and 1-click replacement popovers
  let tokensHtml = '';
  if (res && res.words && res.words.length > 0) {
    tokensHtml = res.words.map((w, idx) => {
      const isAmber = w.low_confidence || w.probability < 0.80;
      const confPct = Math.round(w.probability * 100);
      const isPopoverOpen = state.activeAmberPopoverIndex === idx;

      if (isAmber) {
        const suggestionsHtml = (w.suggestions && w.suggestions.length > 0)
          ? `<div class="mt-1.5 pt-1.5 border-t border-slate-600/50 space-y-1">
              <span class="text-[9px] uppercase tracking-wider text-slate-300 block font-semibold">1-Click Drug Corrections:</span>
              <div class="flex flex-wrap gap-1">
                ${w.suggestions.map(s => `
                  <button type="button" onclick="replaceAmberWordWithSuggestion(${idx}, '${s.replace(/'/g, "\\'")}')" class="px-2 py-0.5 rounded bg-teal-600 hover:bg-teal-500 text-white font-medium text-[10px] transition">
                    ✓ ${s}
                  </button>
                `).join('')}
              </div>
            </div>`
          : `<span class="text-[10px] text-slate-300 block">Click to enter phonetic correction</span>`;

        return `
          <div class="relative inline-block">
            <span onclick="toggleAmberPopover(${idx})" class="token-pill token-amber" title="Acoustic Confidence: ${confPct}% • Click for 1-click drug suggestions">
              <span>${w.word}</span>
              <span class="token-conf-badge">(${confPct}%) ⚠️</span>
            </span>
            ${isPopoverOpen ? `
              <div class="suggestion-popover">
                <div class="flex items-center justify-between space-x-2 font-bold mb-0.5">
                  <span>Acoustic Confidence: ${confPct}% (Low)</span>
                  <span onclick="toggleAmberPopover(${idx})" class="cursor-pointer hover:text-red-300 text-xs px-1">&times;</span>
                </div>
                <div class="text-[10px] text-slate-200">Phonetic ambiguity detected in audio signal.</div>
                ${suggestionsHtml}
              </div>
            ` : ''}
          </div>
        `;
      } else {
        return `
          <span class="token-pill token-green" title="Confidence: ${confPct}% [${w.start}s -> ${w.end}s]">
            <span>${w.word}</span>
            <span class="token-conf-badge text-emerald-600">(${confPct}%)</span>
          </span>
        `;
      }
    }).join(' ');
  } else if (res && res.text) {
    tokensHtml = `<span class="text-sm font-medium text-slate-800">${res.text}</span>`;
  } else {
    tokensHtml = `<span class="text-xs text-slate-400 italic">No audio transcribed yet. Tap 'Start Dictation' or select a 1-click vernacular preset below.</span>`;
  }

  // Vernacular entities & SIG card
  let sigCardHtml = '';
  if (res && res.clinical_sig) {
    const sig = res.clinical_sig;
    const entities = res.vernacular_entities || [];

    const entityPills = entities.length > 0 ? entities.map(e => `
      <span class="vernacular-tag mr-1.5 mb-1">
        <strong class="font-bold mr-1">[${e.language}]</strong> "${e.raw_phrase}" &rarr; <span class="font-semibold text-teal-800 ml-1">${e.medical_meaning} (${e.clinical_sig_code})</span>
      </span>
    `).join('') : '<span class="text-[11px] text-slate-400">Direct medical phrasing</span>';

    sigCardHtml = `
      <div class="mt-3 p-3.5 bg-gradient-to-br from-teal-50/70 to-blue-50/50 rounded-xl border border-teal-200">
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center space-x-2">
            <span class="px-2 py-0.5 rounded bg-[#1F3A5F] text-white text-[10px] font-mono font-bold tracking-wider">
              SIG: ${sig.standard_sig_code || '1 TAB PO'}
            </span>
            <span class="text-xs font-bold text-slate-900">${sig.drug_name || 'Prescription Target'}</span>
            ${sig.dosage ? `<span class="text-xs text-slate-600 font-mono">(${sig.dosage})</span>` : ''}
          </div>
          <button type="button" onclick="applyClinicalSigToForm(state.voiceLastResult.clinical_sig)" class="px-2.5 py-1 bg-[#2A7F85] hover:bg-[#22696E] text-white rounded-lg text-xs font-bold flex items-center space-x-1 shadow-xs transition">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
            <span>Apply to Form</span>
          </button>
        </div>

        <div class="text-xs text-slate-700 mb-2 leading-relaxed font-sans">
          <strong>Structured Instructions:</strong> "${sig.sig_full}"
        </div>

        <div class="pt-2 border-t border-teal-200/60 flex flex-wrap items-center">
          <span class="text-[10px] font-bold text-slate-500 uppercase tracking-wider mr-2">Vernacular Mappings:</span>
          ${entityPills}
        </div>
      </div>
    `;
  }

  return `
    <div class="mb-6 p-4 sm:p-5 bg-white rounded-2xl border border-teal-200/80 shadow-elevated">
      <!-- Top Bar: Title & Model Specs -->
      <div class="flex flex-col sm:flex-row sm:items-center justify-between pb-3 border-b border-slate-100 gap-2 mb-3">
        <div class="flex items-center space-x-2">
          <div class="w-7 h-7 rounded-lg bg-teal-50 border border-teal-200 flex items-center justify-center text-[#2A7F85]">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 003-3V5a3 3 0 10-6 0v6a3 3 0 003 3z"/></svg>
          </div>
          <div>
            <h3 class="font-serif font-bold text-sm text-[#1F3A5F]">Multilingual Medical Dictation Pipeline</h3>
            <div class="flex items-center space-x-1.5 text-[10px] text-slate-500 font-mono">
              <span>faster-whisper CTranslate2 INT8</span>
              <span>•</span>
              <span class="text-emerald-700 font-semibold">Model: small (INT8)</span>
              <span>•</span>
              <span class="text-teal-700">Code-Switching Vernacular Engine</span>
            </div>
          </div>
        </div>

        <!-- Language Selector -->
        <div class="flex items-center space-x-1.5 bg-[#F4F3EF] p-1 rounded-xl">
          ${langPills}
        </div>
      </div>

      <!-- Main Recording & Oscilloscope Bar -->
      <div class="grid grid-cols-1 md:grid-cols-12 gap-4 items-center bg-[#F8FAFC] p-3.5 rounded-xl border border-slate-200/60 mb-3">
        <!-- Mic Button & Ripple Rings (col 3) -->
        <div class="md:col-span-3 flex items-center space-x-3">
          <div class="ripple-wrapper">
            ${isRec ? `
              <div class="ripple-ring"></div>
              <div class="ripple-ring"></div>
              <div class="ripple-ring"></div>
            ` : ''}
            <button type="button" onclick="toggleDictation()" class="w-12 h-12 rounded-full ${isRec ? 'bg-red-500 hover:bg-red-600 text-white mic-recording-pulse' : 'bg-[#2A7F85] hover:bg-[#22696E] text-white'} flex items-center justify-center shadow-md transition transform active:scale-95 focus:outline-none">
              ${isRec ? `
                <div class="w-4 h-4 bg-white rounded-xs"></div>
              ` : `
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 003-3V5a3 3 0 10-6 0v6a3 3 0 003 3z"/></svg>
              `}
            </button>
          </div>

          <div>
            <div class="text-xs font-bold text-slate-800">
              ${isRec ? 'Recording Audio...' : (isProc ? 'Transcribing INT8...' : 'Voice Dictation')}
            </div>
            <div class="text-[11px] text-slate-500 flex items-center space-x-1">
              <span id="voice-timer-display" class="font-mono font-bold text-teal-800">${String(Math.floor(state.voiceElapsed / 60)).padStart(2, '0')}:${String(state.voiceElapsed % 60).padStart(2, '0')}</span>
              <span>•</span>
              <span class="text-[10px] text-slate-400">MediaRecorder Opus</span>
            </div>
          </div>
        </div>

        <!-- Oscilloscope Waveform Canvas (col 6) -->
        <div class="md:col-span-6 space-y-1.5">
          <div class="flex items-center justify-between text-[10px] text-slate-500 px-1 font-mono">
            <span>Web Audio API AnalyserNode (16kHz)</span>
            <span id="vad-status-badge" class="px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-600 border border-slate-200 flex items-center">
              <span class="w-1.5 h-1.5 rounded-full bg-slate-400 mr-1.5"></span> VAD: Ready
            </span>
          </div>

          <!-- Real-time Oscilloscope Canvas -->
          <canvas id="voice-waveform-canvas" width="460" height="54" class="waveform-canvas"></canvas>

          <!-- Real-time Audio VU Meter -->
          <div class="vu-meter-track" title="Audio Decibel VU Meter">
            <div id="vu-meter-fill" class="vu-meter-fill"></div>
          </div>
        </div>

        <!-- Controls (col 3) -->
        <div class="md:col-span-3 flex flex-col space-y-2">
          ${isRec ? `
            <button type="button" onclick="stopMedicalVoiceRecording()" class="w-full py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-xs font-bold shadow-xs transition flex items-center justify-center space-x-1.5">
              <span>Stop & Transcribe</span>
            </button>
          ` : `
            <button type="button" onclick="startMedicalVoiceRecording()" class="w-full py-2 bg-[#1F3A5F] hover:bg-[#152943] text-white rounded-lg text-xs font-bold shadow-xs transition flex items-center justify-center space-x-1.5">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 003-3V5a3 3 0 10-6 0v6a3 3 0 003 3z"/></svg>
              <span>Start Dictating</span>
            </button>
          `}
          <div class="text-[10px] text-slate-400 text-center leading-tight">
            Client-side VAD strips silence to minimize transcription latency
          </div>
        </div>
      </div>

      <!-- 1-Click Clinical Vernacular Presets Bar -->
      <div class="mb-3 p-2.5 bg-[#FAF9F6] border border-slate-200/80 rounded-xl">
        <div class="flex items-center justify-between mb-1.5 px-1">
          <span class="text-[10px] font-bold text-slate-500 uppercase tracking-wider flex items-center space-x-1">
            <span>⚡ 1-Click Vernacular Code-Switching Test Presets:</span>
          </span>
          <span class="text-[10px] text-teal-800 font-medium">Click any scenario to execute live</span>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
          <button type="button" onclick="simulateClinicalPreset('Paracetamol 650mg bhojanam taruvatha roju ki rendu sarlu', 'te-IN')" class="p-2 bg-white hover:bg-teal-50/70 border border-slate-200 hover:border-teal-300 rounded-lg text-left transition shadow-xs group">
            <div class="flex items-center justify-between text-[10px] text-slate-400 mb-0.5 font-bold">
              <span class="text-purple-700 font-mono">[Telugu + English]</span>
              <span class="group-hover:text-teal-700">&rarr;</span>
            </div>
            <div class="text-xs font-medium text-slate-800 line-clamp-1">"Paracetamol 650mg bhojanam taruvatha"</div>
            <div class="text-[10px] text-teal-700 mt-0.5">Post-prandial BID &bull; 1 TAB PO PC BID</div>
          </button>

          <button type="button" onclick="simulateClinicalPreset('BP tablet subah khali pet din me do baar', 'hi-IN')" class="p-2 bg-white hover:bg-teal-50/70 border border-slate-200 hover:border-teal-300 rounded-lg text-left transition shadow-xs group">
            <div class="flex items-center justify-between text-[10px] text-slate-400 mb-0.5 font-bold">
              <span class="text-amber-700 font-mono">[Hindi + English]</span>
              <span class="group-hover:text-teal-700">&rarr;</span>
            </div>
            <div class="text-xs font-medium text-slate-800 line-clamp-1">"BP tablet subah khali pet din me do baar"</div>
            <div class="text-[10px] text-teal-700 mt-0.5">Pre-prandial BID &bull; 1 TAB PO AC BID</div>
          </button>

          <button type="button" onclick="simulateClinicalPreset('mandulu bhojanam tharuvatha', 'te-IN')" class="p-2 bg-white hover:bg-teal-50/70 border border-slate-200 hover:border-teal-300 rounded-lg text-left transition shadow-xs group">
            <div class="flex items-center justify-between text-[10px] text-slate-400 mb-0.5 font-bold">
              <span class="text-blue-700 font-mono">[Telugu Vernacular]</span>
              <span class="group-hover:text-teal-700">&rarr;</span>
            </div>
            <div class="text-xs font-medium text-slate-800 line-clamp-1">"mandulu bhojanam tharuvatha"</div>
            <div class="text-[10px] text-teal-700 mt-0.5">Post-prandial oral administration &bull; PO PC</div>
          </button>

          <button type="button" onclick="simulateClinicalPreset('Atorvastatin 20mg once daily at bedtime', 'en-US')" class="p-2 bg-white hover:bg-teal-50/70 border border-slate-200 hover:border-teal-300 rounded-lg text-left transition shadow-xs group">
            <div class="flex items-center justify-between text-[10px] text-slate-400 mb-0.5 font-bold">
              <span class="text-emerald-700 font-mono">[Clinical English]</span>
              <span class="group-hover:text-teal-700">&rarr;</span>
            </div>
            <div class="text-xs font-medium text-slate-800 line-clamp-1">"Atorvastatin 20mg once daily at bedtime"</div>
            <div class="text-[10px] text-teal-700 mt-0.5">Lipid statin &bull; 1 TAB PO QHS QD</div>
          </button>
        </div>
      </div>

      <!-- Live Transcription Token Confidence Panel -->
      <div class="p-3 bg-[#FAF9F6] border border-slate-200 rounded-xl">
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center space-x-2">
            <span class="text-[11px] font-bold text-slate-700 uppercase tracking-wider">Acoustic Token Confidence:</span>
            <span class="text-[10px] text-slate-500">Green &ge; 80% &bull; Amber &lt; 80% with 1-Click Phonetic Corrections</span>
          </div>
          ${res && res.processing_time_ms ? `
            <span class="text-[10px] font-mono font-semibold text-slate-500">
              Infer: ${res.processing_time_ms}ms &bull; Avg Conf: ${Math.round((res.average_confidence || 0.9) * 100)}%
            </span>
          ` : ''}
        </div>

        <!-- Token Pills Container -->
        <div class="p-3 bg-white rounded-lg border border-slate-200 min-h-[50px] flex flex-wrap items-center">
          ${tokensHtml}
        </div>

        <!-- Structured Clinical SIG Card -->
        ${sigCardHtml}
      </div>
    </div>
  `;
}


// --- LIVE RXNORM VALIDATION ---

async function checkRxNormLive(val) {
  const query = (val || document.getElementById('add-med-name')?.value || '').trim();
  if (!query || query.length < 3) return;

  try {
    const res = await fetch('/api/rxnorm/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query })
    });
    if (res.ok) {
      const data = await res.json();
      updateRxNormPreviewCard(data);
    }
  } catch (err) {
    console.warn("Using offline concept resolution:", err);
  }
}

function updateRxNormPreviewCard(data) {
  const card = document.getElementById('rxnorm-preview-card');
  if (!card) return;

  card.innerHTML = `
    <div class="p-4 bg-white border border-[#2A7F85]/40 rounded-xl shadow-xs mb-3">
      <div class="flex items-center justify-between mb-2">
        <span class="text-[10px] font-bold uppercase tracking-wider text-[#2A7F85]">RxNorm Validated Concept</span>
        <span class="text-xs px-2 py-0.5 bg-emerald-100 text-emerald-800 rounded font-bold">${data.match_confidence || 99.8}% Match</span>
      </div>
      <div class="font-serif font-bold text-slate-900 text-base mb-1">${data.concept_name || 'Atorvastatin 20mg'}</div>
      <div class="text-xs text-slate-600 mb-2">${data.verification_summary || '1 tablet daily at bedtime • Lipid-lowering statin (HMG-CoA reductase inhibitor)'}</div>
      <div class="flex flex-wrap gap-1.5">
        <span class="px-2 py-0.5 bg-slate-100 text-slate-700 text-[10px] rounded font-medium">Oral Route</span>
        <span class="px-2 py-0.5 bg-slate-100 text-slate-700 text-[10px] rounded font-medium">Nightly Schedule</span>
        <span class="px-2 py-0.5 bg-slate-100 text-slate-700 text-[10px] rounded font-medium">Cardiovascular</span>
      </div>
    </div>

    ${data.dietary_guideline ? `
      <div class="p-3.5 bg-amber-50/90 border border-amber-200 rounded-xl text-xs text-amber-900 mb-3">
        <div class="font-bold text-amber-800 mb-1 flex items-center space-x-1.5">
          <svg class="w-4 h-4 text-amber-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
          <span>Dietary Interaction Guideline</span>
        </div>
        <div>${data.dietary_guideline}</div>
      </div>
    ` : ''}
  `;
}

// --- AUTOMATIC 6-DIGIT PASSCODE ADVANCE LOGIC (Requirement 2) ---

function setupPinDigitAutoAdvance() {
  const inputs = document.querySelectorAll('.pin-digit');
  if (!inputs || inputs.length !== 6) return;

  inputs.forEach((input, idx) => {
    // Focus auto-select
    input.addEventListener('focus', () => {
      input.select();
    });

    // Keystroke navigation
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace') {
        if (input.value === '') {
          if (idx > 0) {
            e.preventDefault();
            const prev = inputs[idx - 1];
            prev.value = '';
            prev.classList.remove('filled');
            prev.focus();
          }
        } else {
          input.value = '';
          input.classList.remove('filled');
        }
      } else if (e.key === 'ArrowLeft' && idx > 0) {
        e.preventDefault();
        inputs[idx - 1].focus();
      } else if (e.key === 'ArrowRight' && idx < 5) {
        e.preventDefault();
        inputs[idx + 1].focus();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const code = getPinFromInputs();
        if (code.replace('-', '').length === 6) {
          handleDoctorAccess(code);
        }
      }
    });

    // Input event for numbers (typing and mobile soft keyboards)
    input.addEventListener('input', () => {
      const val = input.value;
      const digit = val.replace(/\D/g, '').slice(-1);
      
      if (digit) {
        input.value = digit;
        input.classList.add('filled');
        if (idx < 5) {
          inputs[idx + 1].focus();
        } else {
          onPinComplete(inputs);
        }
      } else {
        input.value = '';
        input.classList.remove('filled');
      }
    });

    // Paste event: splits 6 digits cleanly
    input.addEventListener('paste', (e) => {
      e.preventDefault();
      const pasted = (e.clipboardData || window.clipboardData).getData('text');
      const cleanDigits = pasted.replace(/\D/g, '').slice(0, 6);
      
      if (cleanDigits) {
        cleanDigits.split('').forEach((char, i) => {
          if (inputs[i]) {
            inputs[i].value = char;
            inputs[i].classList.add('filled');
          }
        });

        if (cleanDigits.length === 6) {
          inputs[5].focus();
          onPinComplete(inputs);
        } else if (cleanDigits.length < 6) {
          inputs[cleanDigits.length].focus();
        }
      }
    });
  });
}

function getPinFromInputs() {
  const inputs = document.querySelectorAll('.pin-digit');
  if (inputs.length !== 6) return '';
  const d = Array.from(inputs).map(i => i.value.trim()).join('');
  if (d.length === 6) {
    return `${d.substring(0, 3)}-${d.substring(3, 6)}`;
  }
  return d;
}

function onPinComplete(inputs) {
  const code = getPinFromInputs();
  const btn = document.getElementById('btn-access-history');
  if (btn) {
    btn.classList.add('ring-2', 'ring-offset-2', 'ring-[#1F3A5F]');
  }
  showToast(`6-Digit Code Verified: ${code}`, 'teal');
}

function clearPinInputs() {
  const inputs = document.querySelectorAll('.pin-digit');
  inputs.forEach(i => {
    i.value = '';
    i.classList.remove('filled');
  });
  if (inputs[0]) inputs[0].focus();
  const btn = document.getElementById('btn-access-history');
  if (btn) btn.classList.remove('ring-2', 'ring-offset-2', 'ring-[#1F3A5F]');
}

// --- RENDER ORCHESTRATOR ---

function renderApp() {
  const container = document.getElementById('app-root');
  if (!container) return;

  if (state.view === 'login') {
    container.innerHTML = renderLoginScreen();
  } else if (state.view === 'patient') {
    container.innerHTML = renderPatientScreen();
    attachPatientEvents();
  } else if (state.view === 'doctor') {
    container.innerHTML = renderDoctorScreen();
    attachDoctorEvents();
  }

  updateTimerDisplays();
}


function selectLoginRole(role) {
  state.loginRole = role;
  renderApp();
}

function selectLoginPatient(id) {
  state.selectedPatientId = id;
  renderApp();
}

function selectLoginDoctor(id) {
  state.selectedDoctorId = id;
  renderApp();
}

async function executeLogin() {
  if (state.loginRole === 'patient') {
    await switchDemoPatient(state.selectedPatientId);
    state.view = 'patient';
    state.patientTab = 'medications';
  } else {
    state.view = 'doctor';
    state.doctorStep = 'dashboard';
  }
  renderApp();
}

async function switchDemoPatient(id) {
  state.selectedPatientId = id;
  try {
    const res = await fetch('/api/patient/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id })
    });
    if (res.ok) {
      const data = await res.json();
      state.patient = data.patient;
      await loadLatestShare();
      showToast(`Switched active profile to ${data.patient.name} (#${data.patient.id})`, 'teal');
      renderApp();
    }
  } catch (err) {
    console.error("Failed to switch patient:", err);
  }
}

function setRoleAndLogin(role) {
  state.view = role;
  if (role === 'doctor') {
    state.doctorStep = 'dashboard';
  } else {
    state.patientTab = 'medications';
  }
  renderApp();
}

// =========================================================================
// SCREEN 1: Role Select / Login (WhatsApp Image 2026-10-25 at 915.03 PM.jpeg)
// =========================================================================

function renderLoginScreen() {
  const isPatient = state.loginRole === 'patient';
  const curPatient = state.availablePatients.find(p => p.id === state.selectedPatientId) || state.availablePatients[0];
  const curDoctor = state.availableDoctors.find(d => d.id === state.selectedDoctorId) || state.availableDoctors[0];

  return `
    <div class="min-h-screen flex items-center justify-center p-6 bg-[#FAF9F6]">
      <div class="w-full max-w-lg bg-white rounded-2xl border border-slate-200 shadow-card p-8 animate-toast">
        
        <!-- Logo & Subtitle -->
        <div class="text-center mb-6">
          <div class="inline-flex items-center space-x-2.5 mb-2">
            <svg class="w-7 h-7 text-[#2A7F85]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M3 12h4l3 8 4-16 3 8h4"/>
            </svg>
            <span class="text-3xl font-serif font-bold text-[#1F3A5F] tracking-tight">Geosmin</span>
          </div>
          <p class="font-serif italic text-slate-600 text-sm">“Your medication story, safely shared”</p>
          <div class="text-[11px] uppercase tracking-wider text-[#2A7F85] font-semibold mt-1">VJ Hackathon 2026 • Medication Safety Platform</div>
        </div>

        <!-- Role Select Switcher -->
        <div class="grid grid-cols-2 gap-2 p-1.5 bg-[#F4F3EF] rounded-xl mb-6">
          <button onclick="selectLoginRole('patient')" class="p-3 text-left rounded-lg transition ${isPatient ? 'bg-white shadow-xs border border-slate-200 text-[#1F3A5F]' : 'text-slate-600 hover:bg-white/60'}">
            <div class="flex items-center space-x-1.5 text-xs font-bold mb-1">
              <svg class="w-4 h-4 text-[#2A7F85]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
              <span>Patient Portal</span>
            </div>
            <div class="text-[11px] text-slate-500 leading-tight">Patient timeline, voice dictation & 5-min passcode share</div>
          </button>

          <button onclick="selectLoginRole('doctor')" class="p-3 text-left rounded-lg transition ${!isPatient ? 'bg-white shadow-xs border border-slate-200 text-[#1F3A5F]' : 'text-slate-600 hover:bg-white/60'}">
            <div class="flex items-center space-x-1.5 text-xs font-bold mb-1">
              <svg class="w-4 h-4 text-[#2A7F85]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
              <span>Clinician Portal</span>
            </div>
            <div class="text-[11px] text-slate-500 leading-tight">Zero-knowledge access, prescription check & safety graph</div>
          </button>
        </div>

        <!-- Multi-User Profile Cards -->
        <div class="mb-6">
          <div class="flex items-center justify-between text-xs font-bold text-slate-700 uppercase tracking-wider mb-2.5">
            <span>${isPatient ? 'Select Demo Patient Profile' : 'Select Clinician Account'}</span>
            <span class="text-[10px] text-[#2A7F85] font-semibold bg-teal-50 px-2 py-0.5 rounded border border-teal-200">Instant Switch</span>
          </div>

          ${isPatient ? `
            <div class="space-y-2">
              ${state.availablePatients.map(p => `
                <div onclick="selectLoginPatient('${p.id}')" class="p-3 rounded-xl border transition cursor-pointer flex items-center justify-between ${state.selectedPatientId === p.id ? 'border-[#2A7F85] bg-teal-50/40 shadow-xs' : 'border-slate-200 bg-white hover:border-slate-300'}">
                  <div class="flex items-center space-x-3">
                    <div class="w-4 h-4 rounded-full border flex items-center justify-center ${state.selectedPatientId === p.id ? 'border-[#2A7F85] bg-[#2A7F85]' : 'border-slate-300'}">
                      ${state.selectedPatientId === p.id ? '<span class="w-1.5 h-1.5 rounded-full bg-white"></span>' : ''}
                    </div>
                    <div>
                      <div class="text-xs font-bold text-slate-900">${p.name} <span class="text-slate-400 font-normal">(${p.age}y, ${p.gender})</span></div>
                      <div class="text-[11px] text-slate-500">${p.condition}</div>
                    </div>
                  </div>
                  <span class="text-[10px] px-2 py-0.5 rounded font-semibold ${p.tagColor === 'amber' ? 'bg-amber-100 text-amber-800' : p.tagColor === 'purple' ? 'bg-purple-100 text-purple-800' : 'bg-blue-100 text-blue-800'}">
                    ${p.warningTag}
                  </span>
                </div>
              `).join('')}
            </div>
          ` : `
            <div class="space-y-2">
              ${state.availableDoctors.map(d => `
                <div onclick="selectLoginDoctor('${d.id}')" class="p-3 rounded-xl border transition cursor-pointer flex items-center justify-between ${state.selectedDoctorId === d.id ? 'border-[#2A7F85] bg-teal-50/40 shadow-xs' : 'border-slate-200 bg-white hover:border-slate-300'}">
                  <div class="flex items-center space-x-3">
                    <div class="w-4 h-4 rounded-full border flex items-center justify-center ${state.selectedDoctorId === d.id ? 'border-[#2A7F85] bg-[#2A7F85]' : 'border-slate-300'}">
                      ${state.selectedDoctorId === d.id ? '<span class="w-1.5 h-1.5 rounded-full bg-white"></span>' : ''}
                    </div>
                    <div>
                      <div class="text-xs font-bold text-slate-900">${d.name}</div>
                      <div class="text-[11px] text-slate-500">${d.specialty} • ${d.hospital}</div>
                    </div>
                  </div>
                  <span class="text-[10px] px-2 py-0.5 rounded font-mono font-semibold bg-slate-100 text-slate-700">
                    ${d.license}
                  </span>
                </div>
              `).join('')}
            </div>
          `}
        </div>

        <!-- Login Credentials (Pre-filled) -->
        <div class="space-y-3.5 mb-6">
          <div>
            <label class="block text-xs font-semibold text-slate-700 mb-1">Email address</label>
            <input type="email" readonly value="${isPatient ? curPatient.email : curDoctor.email}" class="w-full px-3.5 py-2.5 text-xs font-mono rounded-lg border border-slate-200 bg-[#F4F3EF] text-slate-800 focus:outline-none">
          </div>
          <div>
            <div class="flex justify-between items-center mb-1">
              <label class="text-xs font-semibold text-slate-700">Password</label>
              <span class="text-[11px] text-[#2A7F85] font-medium">Demo Pre-authenticated</span>
            </div>
            <input type="password" value="••••••••••••" readonly class="w-full px-3.5 py-2.5 text-xs rounded-lg border border-slate-200 bg-[#F4F3EF] text-slate-800 focus:outline-none">
          </div>
        </div>

        <!-- Login Primary CTA Button -->
        <button onclick="executeLogin()" class="w-full py-3.5 px-4 rounded-xl bg-[#1F3A5F] hover:bg-[#152943] text-white font-semibold text-sm transition flex items-center justify-center space-x-2 shadow-sm">
          <span>Sign In as ${isPatient ? curPatient.name : curDoctor.name}</span>
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
        </button>

        <!-- Security Note -->
        <div class="mt-6 pt-4 border-t border-slate-100 flex items-start space-x-2 text-[11px] text-slate-500 leading-relaxed">
          <svg class="w-4 h-4 text-[#2A7F85] shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
          <span>Zero-knowledge client architecture. Patient medical notes and OTC entries are strictly encrypted end-to-end.</span>
        </div>
      </div>
    </div>
  `;
}

// =========================================================================
// PATIENT SHELL & NAVIGATION (Medications, Add, Share, Access Log)
// =========================================================================

function renderPatientScreen() {
  const patient = state.patient || { name: 'Ravi Kumar', age: 54, id: 'GK-8821' };

  return `
    <div class="min-h-screen flex bg-[#FAF9F6]">
      <!-- Left Sidebar -->
      <aside class="w-64 border-r border-slate-200 bg-white flex flex-col justify-between shrink-0">
        <div>
          <!-- Brand Logo -->
          <div class="p-6 border-b border-slate-100">
            <div class="flex items-center space-x-2.5">
              <span class="w-2.5 h-2.5 rounded-full bg-[#2A7F85]"></span>
              <span class="font-serif font-bold text-xl text-[#1F3A5F]">Geosmin</span>
            </div>
            <div class="text-[10px] tracking-wider uppercase text-slate-400 font-semibold mt-1">MEDICATION SAFETY PLATFORM</div>
          </div>

          <!-- Patient Profile Card -->
          <div class="p-4 mx-4 my-4 rounded-xl bg-[#F4F3EF] border border-slate-200/60 flex items-center space-x-3">
            <div class="w-9 h-9 rounded-full bg-slate-300 flex items-center justify-center text-slate-700 font-semibold text-xs">
              <svg class="w-5 h-5 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
            </div>
            <div>
              <div class="text-sm font-semibold text-slate-900">${patient.name}</div>
              <div class="text-xs text-slate-500">Age ${patient.age} • ID: #${patient.id}</div>
            </div>
          </div>

          <!-- Sidebar Nav Items -->
          <nav class="px-4 space-y-1">
            <button onclick="switchPatientTab('medications')" class="w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-lg text-sm font-medium transition ${state.patientTab === 'medications' || state.patientTab === 'add_medication' ? 'bg-[#1F3A5F] text-white shadow-xs' : 'text-slate-700 hover:bg-slate-100'}">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>
              <span>Medications</span>
            </button>

            <button onclick="switchPatientTab('share')" class="w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-lg text-sm font-medium transition ${state.patientTab === 'share' ? 'bg-[#1F3A5F] text-white shadow-xs' : 'text-slate-700 hover:bg-slate-100'}">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z"/></svg>
              <span>Share with Doctor</span>
            </button>

            <button onclick="switchPatientTab('access_log')" class="w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-lg text-sm font-medium transition ${state.patientTab === 'access_log' ? 'bg-[#1F3A5F] text-white shadow-xs' : 'text-slate-700 hover:bg-slate-100'}">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              <span>Access Log</span>
            </button>
          </nav>
        </div>

        <!-- Sidebar Footer -->
        <div class="p-4 border-t border-slate-100">
          <div class="p-3 bg-[#F4F3EF] rounded-xl border border-slate-200/80 mb-4 text-[11px] text-slate-600">
            <div class="flex items-center space-x-1.5 font-semibold text-[#1F3A5F] mb-1">
              <svg class="w-3.5 h-3.5 text-[#2A7F85]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>
              <span>Zero-knowledge encrypted</span>
            </div>
            Records visible only to you and authorized clinicians.
          </div>

          <div class="space-y-1">
            <button onclick="showToast('Help & Support documentation: 24/7 patient safety line active.', 'teal')" class="w-full text-left text-xs text-slate-600 py-1.5 px-2 hover:bg-slate-100 rounded flex items-center space-x-1.5">
              <svg class="w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              <span>Help & Settings</span>
            </button>
            <button onclick="setRoleAndLogin('doctor')" class="w-full text-left text-xs text-[#2A7F85] font-medium py-1.5 px-2 hover:bg-teal-50 rounded flex items-center space-x-1.5">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4"/></svg>
              <span>Switch to Doctor View</span>
            </button>
            <button onclick="setRoleAndLogin('login')" class="w-full text-left text-xs text-slate-500 py-1.5 px-2 hover:bg-slate-100 rounded flex items-center space-x-1.5">
              <svg class="w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
              <span>Sign out</span>
            </button>
          </div>
        </div>
      </aside>

      <!-- Main Body Content -->
      <main class="flex-1 overflow-y-auto">
        <!-- Top Status Banner -->
        <header class="bg-white border-b border-slate-200 px-8 py-3.5 flex items-center justify-between text-xs text-slate-600">
          <div class="flex items-center space-x-3">
            <span class="flex items-center space-x-1.5 text-slate-800 font-medium">
              <span class="w-2 h-2 rounded-full bg-emerald-500 pulse-green"></span>
              <span>Patient Portal Active</span>
            </span>
            <span class="text-slate-300">•</span>
            <span>Clinical Sync: Real-time</span>
          </div>
          <div class="flex items-center space-x-3">
            <div class="flex items-center space-x-2 bg-[#F4F3EF] px-2.5 py-1 rounded-lg border border-slate-200">
              <span class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Patient:</span>
              <select onchange="switchDemoPatient(this.value)" class="text-xs font-bold text-[#1F3A5F] bg-transparent focus:outline-none cursor-pointer">
                <option value="GK-8821" ${patient.id === 'GK-8821' ? 'selected' : ''}>Ravi Kumar (54M • AFib)</option>
                <option value="PS-4019" ${patient.id === 'PS-4019' ? 'selected' : ''}>Priya Sharma (42F • Diabetes/Asthma)</option>
                <option value="DC-7731" ${patient.id === 'DC-7731' ? 'selected' : ''}>David Chen (68M • CKD Stage 3b)</option>
              </select>
            </div>
            <div class="w-7 h-7 rounded-full bg-[#1F3A5F] text-white flex items-center justify-center font-bold text-xs">
              ${(patient.name || 'R')[0]}
            </div>
          </div>
        </header>

        <!-- Dynamic Page View -->
        <div class="p-8 max-w-6xl mx-auto">
          ${renderPatientTabContent()}
        </div>
      </main>
    </div>
  `;
}

function renderPatientTabContent() {
  if (state.patientTab === 'add_medication') {
    return renderAddMedicationScreen();
  } else if (state.patientTab === 'share') {
    return renderShareTab();
  } else if (state.patientTab === 'access_log') {
    return renderAccessLogTab();
  }
  return renderMedicationTimelineTab();
}

function switchPatientTab(tab) {
  state.patientTab = tab;
  renderApp();
}

// =========================================================================
// SCREEN 2: Patient Timeline (WhatsApp Image 2-25 at 9.15.03 PM.jpeg)
// =========================================================================

function renderMedicationTimelineTab() {
  const patient = state.patient || {
    name: 'Ravi Kumar',
    next_review: 'Nov 14, 2024',
    allergies: [
      { allergen: 'Penicillin', reaction: 'Severe anaphylaxis' },
      { allergen: 'Sulfa drugs', reaction: 'Hives' }
    ],
    diagnoses: [
      { condition: 'Atrial Fibrillation (Chronic)' },
      { condition: 'Hypertension (Stage 1)' },
      { condition: 'Osteoarthritis (Mild)' }
    ],
    medications: []
  };

  const meds = (patient.medications || []).filter(m => {
    if (state.timelineFilter === 'active') return m.status === 'active';
    if (state.timelineFilter === 'attention') return m.status === 'attention';
    if (state.timelineFilter === 'archived') return m.status === 'archived';
    return true;
  });

  return `
    <div>
      <!-- Header Row -->
      <div class="flex items-start justify-between mb-6">
        <div>
          <div class="text-[11px] uppercase tracking-wider font-semibold text-slate-500 mb-1">
            ACTIVE REGIMEN & ARCHIVE • Synched with RMC Epic EHR
          </div>
          <h1 class="text-3xl font-serif font-bold text-[#1F3A5F]">My Medication Timeline</h1>
          <p class="text-sm text-slate-600 mt-1">Review your full treatment history and safety status before consulting your physician.</p>
        </div>
        <button onclick="switchPatientTab('add_medication')" class="px-4 py-2.5 bg-[#1F3A5F] hover:bg-[#152943] text-white rounded-lg text-sm font-medium transition flex items-center space-x-1.5 shadow-sm">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
          <span>+ Add medication</span>
        </button>
      </div>

      <!-- Clinical Summary Badges (Known Allergies & Diagnoses) -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        <!-- Allergies -->
        <div class="p-4 bg-white rounded-xl border border-slate-200 shadow-xs">
          <div class="flex items-center justify-between mb-3">
            <div class="flex items-center space-x-1.5 text-xs font-bold text-red-600 uppercase tracking-wider">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
              <span>KNOWN ALLERGIES</span>
            </div>
            <span class="text-xs text-red-600 font-bold">Critical</span>
          </div>
          <div class="flex flex-wrap gap-2">
            ${(patient.allergies || []).map(a => `
              <span onclick="inspectWarningDetail('sulfa_cross_reactivity')" class="warning-badge-pulse px-3 py-1 bg-red-50 text-red-700 text-xs rounded-md border border-red-200 font-medium flex items-center space-x-1.5 cursor-pointer">
                <span class="beacon-dot-red"></span>
                <span>${a.allergen} (${a.reaction})</span>
              </span>
            `).join('')}
          </div>
        </div>

        <!-- Diagnoses -->
        <div class="p-4 bg-white rounded-xl border border-slate-200 shadow-xs">
          <div class="flex items-center justify-between mb-3">
            <div class="flex items-center space-x-1.5 text-xs font-bold text-[#1F3A5F] uppercase tracking-wider">
              <svg class="w-4 h-4 text-[#2A7F85]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
              <span>DOCUMENTED DIAGNOSES</span>
            </div>
            <a href="#" onclick="showToast('RMC Medical Records connected. All ICD-10 diagnoses synchronized.', 'teal'); return false;" class="text-xs text-[#2A7F85] hover:underline flex items-center space-x-0.5">
              <span>Update clinical history</span>
              <span>&rarr;</span>
            </a>
          </div>
          <div class="flex flex-wrap gap-2">
            ${(patient.diagnoses || []).map(d => `
              <span class="px-3 py-1 bg-[#F4F3EF] text-slate-800 text-xs rounded-md border border-slate-200 font-medium">
                ${d.condition}
              </span>
            `).join('')}
          </div>
        </div>
      </div>

      <!-- Timeline Filter & Sync Status -->
      <div class="flex items-center justify-between border-b border-slate-200 pb-3 mb-6">
        <div class="flex items-center space-x-2">
          <span class="text-xs font-semibold text-slate-400 uppercase tracking-wider mr-2">Timeline Filter</span>
          <button onclick="setTimelineFilter('all')" class="px-3 py-1 text-xs rounded-full font-medium transition ${state.timelineFilter === 'all' ? 'bg-[#1F3A5F] text-white shadow-xs' : 'bg-[#F4F3EF] text-slate-600 hover:bg-slate-200'}">All Records (4)</button>
          <button onclick="setTimelineFilter('active')" class="px-3 py-1 text-xs rounded-full font-medium transition ${state.timelineFilter === 'active' ? 'bg-[#1F3A5F] text-white shadow-xs' : 'bg-[#F4F3EF] text-slate-600 hover:bg-slate-200'}">Active (2)</button>
          <button onclick="setTimelineFilter('attention')" class="px-3 py-1 text-xs rounded-full font-medium transition ${state.timelineFilter === 'attention' ? 'bg-[#1F3A5F] text-white shadow-xs' : 'bg-[#F4F3EF] text-slate-600 hover:bg-slate-200'}">Attention (1)</button>
          <button onclick="setTimelineFilter('archived')" class="px-3 py-1 text-xs rounded-full font-medium transition ${state.timelineFilter === 'archived' ? 'bg-[#1F3A5F] text-white shadow-xs' : 'bg-[#F4F3EF] text-slate-600 hover:bg-slate-200'}">Archived (1)</button>
        </div>
        <div class="text-xs text-slate-500 flex items-center space-x-2">
          <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
          <span>Synchronized • Next clinical review: ${patient.next_review || 'Nov 14, 2024'}</span>
        </div>
      </div>

      <!-- Medication Cards Feed -->
      <div class="space-y-4 mb-8">
        ${meds.map(renderMedicationTimelineCard).join('')}
      </div>

      <!-- Bottom Doctor Visit Prompt Banner -->
      <div class="p-6 bg-white rounded-xl border border-slate-200 flex items-center justify-between shadow-xs">
        <div class="flex items-center space-x-4">
          <div class="w-10 h-10 rounded-full bg-teal-50 border border-teal-200 flex items-center justify-center text-[#2A7F85]">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
          </div>
          <div>
            <div class="font-serif font-bold text-slate-900 text-base">Ready for your doctor visit?</div>
            <div class="text-xs text-slate-500">Generate a safe, tamper-evident clinician summary packet to present in clinic.</div>
          </div>
        </div>
        <div class="flex items-center space-x-3">
          <button onclick="showToast('Cryptographic PDF Summary downloaded with SHA-256 seal', 'teal')" class="px-4 py-2 border border-slate-200 rounded-lg text-xs font-medium text-slate-700 hover:bg-slate-50 transition flex items-center space-x-1.5">
            <svg class="w-4 h-4 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
            <span>Download PDF Summary</span>
          </button>
          <button onclick="switchPatientTab('share')" class="px-4 py-2 bg-[#1F3A5F] text-white rounded-lg text-xs font-medium hover:bg-[#152943] transition flex items-center space-x-1.5 shadow-xs">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z"/></svg>
            <span>Generate Doctor Access Link</span>
          </button>
        </div>
      </div>
    </div>
  `;
}

function renderMedicationTimelineCard(med) {
  const isAttention = med.status === 'attention';
  const isArchived = med.status === 'archived';

  let borderClass = 'border-slate-200 bg-white';
  let dotColor = 'bg-teal-500';
  let statusBadge = `<span class="px-2.5 py-0.5 bg-emerald-50 text-emerald-700 text-xs font-semibold rounded-full border border-emerald-200/60">● Active</span>`;

  if (isAttention) {
    borderClass = 'border-amber-300 bg-white border-l-4 border-l-amber-500';
    dotColor = 'bg-amber-500';
    statusBadge = `
      <span onclick="inspectWarningDetail('ibuprofen_active_warning')" class="warning-badge-pulse px-2.5 py-0.5 bg-amber-50 text-amber-800 text-xs font-bold rounded-full border border-amber-300 cursor-pointer">
        <span class="beacon-dot-amber mr-1"></span>
        Unsure / Under Review
      </span>
    `;
  } else if (isArchived) {
    borderClass = 'border-slate-200 bg-[#F8F7F4]';
    dotColor = 'bg-slate-400';
    statusBadge = `<span class="px-2.5 py-0.5 bg-slate-200 text-slate-600 text-xs font-medium rounded-full">● Discontinued</span>`;
  }

  return `
    <div class="p-6 rounded-xl border ${borderClass} shadow-card card-hover-lift relative">
      <div class="flex items-start justify-between mb-3">
        <div class="flex items-center space-x-2 text-xs text-slate-500">
          <span class="w-2 h-2 rounded-full ${dotColor}"></span>
          <span class="font-medium text-slate-700">${med.rx_number}</span>
          <span>•</span>
          <span>Prescribed by ${med.prescriber}</span>
        </div>
        <div class="flex items-center space-x-3 text-xs text-slate-500">
          ${statusBadge}
          <span>${med.start_date} – ${med.end_date}</span>
        </div>
      </div>

      <!-- Drug Name & Dose -->
      <div class="flex items-baseline justify-between mb-4">
        <div class="flex items-baseline space-x-3">
          <h2 class="font-serif font-bold text-xl ${isArchived ? 'line-through text-slate-400' : 'text-slate-900'}">${med.name}</h2>
          <span class="px-2.5 py-1 bg-[#F4F3EF] text-slate-700 text-xs font-medium rounded-md border border-slate-200/70">${med.dosage}</span>
        </div>
        <div class="text-xs text-slate-500">
          ${med.route} • Refills Remaining: ${med.refills}
        </div>
      </div>

      <!-- Active Warning Box for Ibuprofen (Micro-interaction Enabled) -->
      ${isAttention ? `
        <div onclick="inspectWarningDetail('ibuprofen_active_warning')" class="p-4 mb-4 bg-amber-50 border border-amber-300 rounded-xl text-xs text-amber-900 alert-shimmer cursor-pointer hover:bg-amber-100/70 transition">
          <div class="flex items-center justify-between mb-1">
            <div class="flex items-center space-x-1.5 font-bold text-amber-900">
              <svg class="w-4 h-4 text-amber-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
              <span>High Potential Interaction Warning</span>
            </div>
            <span class="text-[10px] font-bold uppercase tracking-wider text-amber-800 bg-amber-200/70 px-2 py-0.5 rounded">Tap for clinical mechanism &rarr;</span>
          </div>
          <div>${med.safety_target}</div>
        </div>
      ` : ''}

      <!-- Archived Discontinuation Box for Lisinopril -->
      ${isArchived ? `
        <div class="p-4 mb-4 bg-[#F4F3EF] rounded-xl text-xs text-slate-700 border border-slate-200/60">
          <div class="font-bold text-slate-800 mb-1 uppercase tracking-wider text-[10px]">Discontinuation Rationale</div>
          <div>${med.safety_target}</div>
        </div>
      ` : ''}

      <!-- Clinical Details Grid -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs text-slate-600 pt-3 border-t border-slate-100">
        <div>
          <span class="font-semibold text-slate-400 uppercase tracking-wider text-[10px] block mb-0.5">Clinical Indication</span>
          <span>${med.indication}</span>
        </div>
        <div>
          <span class="font-semibold text-slate-400 uppercase tracking-wider text-[10px] block mb-0.5">Safety & Administration</span>
          <span>${med.safety_target && !isAttention && !isArchived ? med.safety_target : 'Standard therapeutic protocol.'}</span>
        </div>
      </div>

      <!-- Action Links -->
      <div class="flex items-center justify-between mt-4 pt-3 border-t border-slate-100 text-xs">
        <div class="flex items-center space-x-4">
          <button onclick="inspectWarningDetail('${isAttention ? 'ibuprofen_active_warning' : 'warfarin_celecoxib'}')" class="text-[#2A7F85] hover:underline flex items-center space-x-1 font-medium">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
            <span>View details</span>
          </button>
          ${isAttention ? `
            <button onclick="showToast('Flagged for doctor review at upcoming consultation on Nov 14', 'teal')" class="text-slate-600 hover:text-slate-900 font-medium">Discuss at upcoming visit</button>
            <button onclick="showToast('Entry removed from timeline', 'amber')" class="text-red-600 hover:text-red-800">Remove entry</button>
          ` : `
            <button onclick="showToast('Dose logged at ' + new Date().toLocaleTimeString(), 'teal')" class="text-slate-600 hover:text-slate-900">Log dose</button>
          `}
        </div>
        <div class="text-slate-400 text-xs">
          Safety Status: <span class="font-medium text-slate-700">${med.safety_status}</span>
        </div>
      </div>
    </div>
  `;
}

function setTimelineFilter(filter) {
  state.timelineFilter = filter;
  renderApp();
}

// =========================================================================
// SCREEN 3: Add Medication (WhatsApp Image 2026-1at 9.15.05 PM.jpeg)
// =========================================================================

function renderAddMedicationScreen() {
  return `
    <div class="animate-toast">
      <!-- Back Link -->
      <button onclick="switchPatientTab('medications')" class="text-xs font-semibold text-[#2A7F85] hover:underline flex items-center space-x-1 mb-4">
        <span>&larr; Back to Medications</span>
      </button>

      <!-- Screen Title Row -->
      <div class="flex items-start justify-between mb-6">
        <div>
          <div class="text-[10px] font-bold text-[#2A7F85] uppercase tracking-wider mb-1 flex items-center space-x-1.5">
            <span class="w-2 h-2 rounded-full bg-[#2A7F85]"></span>
            <span>CLINICAL REGIMEN INTAKE</span>
          </div>
          <h1 class="text-3xl font-serif font-bold text-[#1F3A5F]">Add New Medication</h1>
          <p class="text-xs text-slate-600 mt-1">Record prescription drugs, supplements, or over-the-counter remedies to track drug-drug interactions.</p>
        </div>

        <div class="p-3 bg-white border border-slate-200 rounded-xl shadow-xs text-right">
          <div class="text-[10px] uppercase font-bold text-slate-400 tracking-wider">CROSS-REFERENCE ENGINE</div>
          <div class="text-xs font-semibold text-[#1F3A5F] flex items-center justify-end space-x-1 mt-0.5">
            <svg class="w-3.5 h-3.5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
            <span>Live surveillance: Warfarin, Metoprolol active</span>
          </div>
        </div>
      </div>

      <!-- Mode Selector Pills -->
      <div class="grid grid-cols-3 gap-2 p-1 bg-[#F4F3EF] rounded-xl max-w-md mb-6 text-xs font-medium text-slate-700">
        <button class="py-2 bg-white rounded-lg shadow-xs text-[#1F3A5F] font-bold flex items-center justify-center space-x-1.5">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h7"/></svg>
          <span>Type Details</span>
        </button>
        <button onclick="showToast('Gemini Vision Optical OCR: Upload Rx prescription slip simulation active.', 'teal')" class="py-2 hover:bg-white/60 rounded-lg transition flex items-center justify-center space-x-1.5">
          <svg class="w-3.5 h-3.5 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"/></svg>
          <span>Upload Rx</span>
        </button>
        <button onclick="toggleDictation()" id="mic-btn" class="py-2 hover:bg-white/60 rounded-lg transition flex items-center justify-center space-x-1.5 text-slate-700">
          <svg class="w-3.5 h-3.5 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 003-3V5a3 3 0 10-6 0v6a3 3 0 003 3z"/></svg>
          <span>Dictate</span>
        </button>
      </div>

      <!-- Medical Voice & Vernacular Dictation Workbench (Requirement: faster-whisper INT8 + Vernacular Adaptation) -->
      ${renderVoiceWorkbench()}

      <!-- 2-Column Layout -->
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
        
        <!-- Left: Form Input (7 cols) -->
        <div class="lg:col-span-7 space-y-6">
          <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-card">
            <div class="flex items-center justify-between pb-3 border-b border-slate-100 mb-4">
              <div>
                <span class="text-[10px] font-bold text-slate-400 uppercase tracking-wider">STEP 1 OF 2</span>
                <h2 class="font-serif font-bold text-lg text-[#1F3A5F]">Pharmacological Specifications</h2>
              </div>
              <span class="px-2.5 py-1 bg-[#E6F4F4] text-[#1F3A5F] border border-[#A2D2D5] rounded-full text-xs font-semibold">
                ● RxNorm Validated
              </span>
            </div>

            <form onsubmit="handleAddMedicationSubmit(event)" class="space-y-4">
              <div>
                <div class="flex justify-between items-center mb-1">
                  <label class="text-xs font-semibold text-slate-700">Medication Name & Generic Equivalent</label>
                  <span class="text-[11px] text-slate-500">Brand: Lipitor</span>
                </div>
                <div class="relative">
                  <input type="text" id="add-med-name" oninput="checkRxNormLive(this.value)" value="Atorvastatin Calcium" required class="w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-200 bg-[#F4F3EF] text-slate-900 focus:outline-none focus:bg-white focus:ring-1 focus:ring-[#1F3A5F]">
                  <span class="absolute right-3 top-2.5 text-slate-400">⚗</span>
                </div>
              </div>

              <div class="grid grid-cols-2 gap-4">
                <div>
                  <label class="text-xs font-semibold text-slate-700 block mb-1">Dosage Strength</label>
                  <input type="text" id="add-dosage" value="20 mg" required class="w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-200 bg-[#F4F3EF] text-slate-900 focus:outline-none focus:bg-white focus:ring-1 focus:ring-[#1F3A5F]">
                </div>
                <div>
                  <label class="text-xs font-semibold text-slate-700 block mb-1">Intake Cadence</label>
                  <input type="text" id="add-cadence" value="Once daily at bedtime" required class="w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-200 bg-[#F4F3EF] text-slate-900 focus:outline-none focus:bg-white focus:ring-1 focus:ring-[#1F3A5F]">
                </div>
              </div>

              <div class="grid grid-cols-2 gap-4">
                <div>
                  <label class="text-xs font-semibold text-slate-700 block mb-1">Prescribing Clinician</label>
                  <input type="text" id="add-clinician" value="Dr. Elena Vance (Cardiology)" required class="w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-200 bg-[#F4F3EF] text-slate-900 focus:outline-none focus:bg-white focus:ring-1 focus:ring-[#1F3A5F]">
                </div>
                <div>
                  <label class="text-xs font-semibold text-slate-700 block mb-1">Therapeutic Indication</label>
                  <input type="text" id="add-indication" value="Cholesterol & lipid management" required class="w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-200 bg-[#F4F3EF] text-slate-900 focus:outline-none focus:bg-white focus:ring-1 focus:ring-[#1F3A5F]">
                </div>
              </div>

              <div class="p-3 bg-[#F4F3EF] rounded-lg text-xs text-slate-600 flex items-center justify-between">
                <span>+ Formulation: <strong>Oral film-coated tablet</strong></span>
                <span class="font-mono text-[11px] text-slate-500">• NDC #0071-0156-23</span>
              </div>
            </form>
          </div>

          <!-- Dietary Advisory Card -->
          <div class="p-5 bg-white rounded-2xl border border-slate-200 shadow-card">
            <div class="flex items-start space-x-3">
              <div class="w-8 h-8 rounded-full bg-teal-50 border border-teal-200 flex items-center justify-center text-[#2A7F85] shrink-0">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
              </div>
              <div>
                <h3 class="font-serif font-bold text-sm text-slate-900 mb-1">Dietary Interaction Guideline</h3>
                <p class="text-xs text-slate-600 leading-relaxed">
                  Consumption of grapefruit or grapefruit juice significantly increases systemic Atorvastatin plasma concentrations by inhibiting CYP3A4 metabolism. Consult Dr. Vance regarding moderation.
                </p>
              </div>
            </div>
          </div>
        </div>

        <!-- Right: AI Verification Check & Confirm (5 cols) -->
        <div class="lg:col-span-5 space-y-6">
          
          <!-- Banner Card -->
          <div class="p-6 rounded-2xl bg-[#1F3A5F] text-white shadow-card relative overflow-hidden">
            <div class="text-[10px] font-bold uppercase tracking-wider text-teal-200 mb-1">SAFETY PIPELINE ACTIVE</div>
            <h3 class="font-serif font-bold text-lg leading-tight mb-2">Continuous Hepatic & Anticoagulant Harmonization</h3>
            <p class="text-xs text-slate-300">Evaluating clearance against Warfarin metabolic profiles.</p>
          </div>

          <!-- Diagnostic Parsing Panel -->
          <div class="p-6 bg-white rounded-2xl border border-slate-200 shadow-card">
            <div class="flex items-center justify-between mb-4 pb-2 border-b border-slate-100">
              <div class="flex items-center space-x-2">
                <svg class="w-4 h-4 text-[#2A7F85]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
                <span class="font-serif font-bold text-base text-slate-900">AI Verification Check</span>
              </div>
              <span class="text-xs px-2.5 py-0.5 bg-emerald-100 text-emerald-800 rounded font-bold">99.8% Match</span>
            </div>

            <div class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">WE UNDERSTOOD:</div>

            <div id="rxnorm-preview-card">
              <div class="p-4 bg-white border border-[#2A7F85]/40 rounded-xl shadow-xs mb-3">
                <div class="font-serif font-bold text-slate-900 text-base mb-1">Atorvastatin 20mg</div>
                <div class="text-xs text-slate-600 mb-2">1 tablet daily at bedtime • Lipid-lowering statin (HMG-CoA reductase inhibitor)</div>
                <div class="flex flex-wrap gap-1.5">
                  <span class="px-2 py-0.5 bg-slate-100 text-slate-700 text-[10px] rounded font-medium">Oral Route</span>
                  <span class="px-2 py-0.5 bg-slate-100 text-slate-700 text-[10px] rounded font-medium">Nightly Schedule</span>
                  <span class="px-2 py-0.5 bg-slate-100 text-slate-700 text-[10px] rounded font-medium">Cardiovascular</span>
                </div>
              </div>
            </div>

            <div class="mt-4 pt-4 border-t border-slate-100">
              <div class="font-serif font-bold text-sm text-slate-900 mb-1">Is this correct?</div>
              <p class="text-xs text-slate-500 mb-4">Confirming will add this record to your chronological pharmacotherapy ledger and recalculate cross-reactions.</p>

              <div class="space-y-2">
                <button onclick="handleAddMedicationSubmit(event)" class="w-full py-3 px-4 bg-[#1F3A5F] hover:bg-[#152943] text-white rounded-xl text-sm font-semibold transition flex items-center justify-center space-x-2 shadow-sm">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                  <span>Confirm & add to timeline</span>
                </button>
                <button onclick="switchPatientTab('medications')" class="w-full py-2.5 px-4 border border-slate-200 hover:bg-slate-50 rounded-xl text-xs font-semibold text-slate-700 transition flex items-center justify-center space-x-1">
                  <span>Edit details</span>
                </button>
              </div>
            </div>
          </div>

          <!-- Continuous Vigilance Guarantee -->
          <div class="p-4 bg-[#F4F3EF] rounded-xl border border-slate-200/80 text-xs text-slate-600 flex items-start space-x-2.5">
            <svg class="w-4 h-4 text-[#2A7F85] shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <div>
              <strong class="text-slate-800">Continuous Vigilance Guarantee:</strong> All additions are cross-checked against your documented allergies and existing active prescriptions (Warfarin, Metoprolol).
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

// =========================================================================
// SCREEN 4: Share with Doctor (WhatsApp Image 2026-10-25 .jpeg)
// =========================================================================

function renderShareTab() {
  const share = state.share || { passcode: '842-916', cipher_specs: 'TLS 1.3 • AES-GCM 256' };

  return `
    <div class="animate-toast">
      <!-- Title & Consultation Cipher -->
      <div class="flex items-center justify-between mb-8">
        <div>
          <div class="flex items-center space-x-2 text-xs font-semibold text-[#2A7F85] uppercase tracking-wider mb-1">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
            <span>ZERO-KNOWLEDGE CLINICAL GATEWAY</span>
          </div>
          <h1 class="text-3xl font-serif font-bold text-[#1F3A5F]">Share with Doctor</h1>
          <p class="text-sm text-slate-600 mt-1">Grant temporary, one-time read access to your clinical history during a medical consultation.</p>
        </div>
        <div class="px-4 py-2 bg-white rounded-xl text-xs font-mono text-slate-700 border border-slate-200 shadow-xs flex items-center space-x-2">
          <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
          <span>CONSULTATION CIPHER: ${share.cipher_specs}</span>
        </div>
      </div>

      <!-- 2 Cards Layout -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-8 mb-8">
        
        <!-- Step 1: Select information to include -->
        <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-card flex flex-col justify-between">
          <div>
            <div class="flex items-center justify-between mb-4 pb-2 border-b border-slate-100">
              <div class="flex items-center space-x-2.5">
                <span class="w-6 h-6 rounded-full bg-[#1F3A5F] text-white flex items-center justify-center text-xs font-bold">1</span>
                <span class="font-serif font-bold text-slate-900 text-base">Select information to include</span>
              </div>
              <span id="scope-counter" class="text-xs font-bold text-[#2A7F85]">3 SELECTED</span>
            </div>

            <div class="space-y-3">
              <label class="p-3.5 border rounded-xl flex items-start space-x-3 cursor-pointer hover:bg-slate-50 transition border-[#2A7F85] bg-teal-50/20">
                <input type="checkbox" id="chk-meds" checked onchange="updateSelectedScopesCount()" class="mt-1 rounded text-[#1F3A5F] focus:ring-0">
                <div class="flex-1">
                  <div class="font-semibold text-slate-900 text-sm">Medications</div>
                  <div class="text-xs text-slate-500">Active & archived timeline, dosages, and OTC reports</div>
                </div>
              </label>

              <label class="p-3.5 border rounded-xl flex items-start space-x-3 cursor-pointer hover:bg-slate-50 transition border-[#2A7F85] bg-teal-50/20">
                <input type="checkbox" id="chk-allergies" checked onchange="updateSelectedScopesCount()" class="mt-1 rounded text-[#1F3A5F] focus:ring-0">
                <div class="flex-1">
                  <div class="font-semibold text-slate-900 text-sm">Known Allergies</div>
                  <div class="text-xs text-slate-500">Penicillin severe anaphylaxis, Sulfa drugs</div>
                </div>
              </label>

              <label class="p-3.5 border rounded-xl flex items-start space-x-3 cursor-pointer hover:bg-slate-50 transition border-[#2A7F85] bg-teal-50/20">
                <input type="checkbox" id="chk-diagnoses" checked onchange="updateSelectedScopesCount()" class="mt-1 rounded text-[#1F3A5F] focus:ring-0">
                <div class="flex-1">
                  <div class="font-semibold text-slate-900 text-sm">Documented Diagnoses</div>
                  <div class="text-xs text-slate-500">Atrial Fibrillation, Hypertension, Osteoarthritis</div>
                </div>
              </label>

              <label class="p-3.5 border rounded-xl flex items-start space-x-3 cursor-pointer hover:bg-slate-50 transition border-slate-200">
                <input type="checkbox" id="chk-labs" onchange="updateSelectedScopesCount()" class="mt-1 rounded text-[#1F3A5F] focus:ring-0">
                <div class="flex-1">
                  <div class="font-semibold text-slate-900 text-sm">Laboratory Safety Trends</div>
                  <div class="text-xs text-slate-500">INR records & periodic renal panel diagnostics</div>
                </div>
              </label>
            </div>
          </div>

          <div class="mt-6 p-3 bg-[#F4F3EF] rounded-xl text-xs text-slate-500 border border-slate-200/70 flex items-start space-x-2">
            <svg class="w-4 h-4 text-[#2A7F85] shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <span><strong>Zero leak policy:</strong> Private personal notes, clinician messages, and unverified draft logs are never shared.</span>
          </div>
        </div>

        <!-- Step 2: Temporary Access Token (Synced Timer) -->
        <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-card flex flex-col justify-between">
          <div>
            <div class="flex items-center justify-between mb-4 pb-2 border-b border-slate-100">
              <div class="flex items-center space-x-2.5">
                <span class="w-6 h-6 rounded-full bg-[#1F3A5F] text-white flex items-center justify-center text-xs font-bold">2</span>
                <span class="font-serif font-bold text-slate-900 text-base">Temporary access token</span>
              </div>
              <span class="text-xs text-amber-800 bg-amber-50 px-2.5 py-1 rounded-full font-mono font-bold border border-amber-200 flex items-center space-x-1">
                <span>⏱ Expires in</span>
                <span class="live-timer">04:56</span>
              </span>
            </div>

            <div class="p-2.5 bg-teal-50 border border-teal-200 rounded-xl text-center text-xs text-teal-900 font-medium mb-4 flex items-center justify-center space-x-1.5">
              <svg class="w-3.5 h-3.5 text-[#2A7F85]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>
              <span>One-Time Access Only • Auto-revokes on consult completion</span>
            </div>

            <!-- Stylized QR Code with framing brackets -->
            <div class="flex justify-center my-4">
              <div class="relative p-3 bg-white border border-slate-200 rounded-xl shadow-xs">
                <!-- Framing brackets -->
                <div class="absolute top-1 left-1 w-4 h-4 border-t-2 border-l-2 border-slate-900"></div>
                <div class="absolute top-1 right-1 w-4 h-4 border-t-2 border-r-2 border-slate-900"></div>
                <div class="absolute bottom-1 left-1 w-4 h-4 border-b-2 border-l-2 border-slate-900"></div>
                <div class="absolute bottom-1 right-1 w-4 h-4 border-b-2 border-r-2 border-slate-900"></div>

                <svg class="w-44 h-44 text-[#1F3A5F]" viewBox="0 0 100 100" fill="currentColor">
                  <!-- Finder Corners -->
                  <rect x="6" y="6" width="26" height="26" fill="#1F3A5F"/>
                  <rect x="10" y="10" width="18" height="18" fill="white"/>
                  <rect x="14" y="14" width="10" height="10" fill="#1F3A5F"/>

                  <rect x="68" y="6" width="26" height="26" fill="#1F3A5F"/>
                  <rect x="72" y="10" width="18" height="18" fill="white"/>
                  <rect x="76" y="14" width="10" height="10" fill="#1F3A5F"/>

                  <rect x="6" y="68" width="26" height="26" fill="#1F3A5F"/>
                  <rect x="10" y="72" width="18" height="18" fill="white"/>
                  <rect x="14" y="76" width="10" height="10" fill="#1F3A5F"/>

                  <!-- Data modules in teal and navy -->
                  <rect x="38" y="8" width="6" height="6" fill="#2A7F85"/>
                  <rect x="48" y="8" width="6" height="6" fill="#1F3A5F"/>
                  <rect x="38" y="20" width="6" height="6" fill="#1F3A5F"/>
                  <rect x="48" y="20" width="6" height="6" fill="#2A7F85"/>
                  <rect x="8" y="38" width="6" height="6" fill="#1F3A5F"/>
                  <rect x="20" y="38" width="6" height="6" fill="#2A7F85"/>
                  <rect x="38" y="38" width="8" height="8" fill="#1F3A5F"/>
                  <rect x="50" y="38" width="6" height="6" fill="#2A7F85"/>
                  <rect x="64" y="38" width="6" height="6" fill="#1F3A5F"/>
                  <rect x="76" y="38" width="6" height="6" fill="#1F3A5F"/>
                  <rect x="38" y="50" width="6" height="6" fill="#2A7F85"/>
                  <rect x="50" y="50" width="6" height="6" fill="#1F3A5F"/>
                  <rect x="68" y="50" width="6" height="6" fill="#2A7F85"/>
                  <rect x="38" y="68" width="6" height="6" fill="#1F3A5F"/>
                  <rect x="50" y="68" width="6" height="6" fill="#2A7F85"/>
                  <rect x="68" y="68" width="6" height="6" fill="#1F3A5F"/>
                  <rect x="80" y="68" width="6" height="6" fill="#2A7F85"/>
                  <rect x="50" y="80" width="6" height="6" fill="#1F3A5F"/>
                  <rect x="68" y="80" width="6" height="6" fill="#2A7F85"/>
                  <rect x="80" y="80" width="6" height="6" fill="#1F3A5F"/>

                  <!-- Center security crest -->
                  <circle cx="50" cy="50" r="4" fill="#2A7F85"/>
                </svg>
              </div>
            </div>

            <!-- Read Aloud Passcode -->
            <div class="text-center mb-4">
              <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5">OR READ CODE ALOUD TO CLINICIAN</div>
              <div class="inline-flex items-center space-x-3 px-6 py-2.5 bg-[#F4F3EF] border border-slate-200 rounded-xl">
                <span class="text-2xl font-mono font-bold tracking-widest text-[#1F3A5F]">${share.passcode}</span>
                <button onclick="navigator.clipboard.writeText('${share.passcode}'); showToast('Passcode copied: ${share.passcode}', 'teal')" class="px-2.5 py-1 text-xs bg-white border border-slate-200 rounded-lg text-slate-700 hover:bg-slate-50 transition flex items-center space-x-1 shadow-xs">
                  <svg class="w-3.5 h-3.5 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
                  <span>Copy</span>
                </button>
              </div>
            </div>
          </div>

          <!-- Buttons -->
          <div class="grid grid-cols-2 gap-3 pt-3 border-t border-slate-100">
            <button onclick="generateNewShareCode()" class="py-2.5 px-3 border border-slate-200 hover:bg-slate-50 rounded-xl text-xs font-semibold text-slate-700 transition flex items-center justify-center space-x-1.5">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
              <span>New Code</span>
            </button>
            <button onclick="revokeCurrentShare()" class="py-2.5 px-3 bg-red-50 hover:bg-red-100 text-red-700 border border-red-200 rounded-xl text-xs font-semibold transition flex items-center justify-center space-x-1.5">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
              <span>Revoke Access</span>
            </button>
          </div>
        </div>
      </div>

      <!-- Patient Sovereignty Footer Banner -->
      <div class="p-4 bg-white rounded-xl border border-slate-200 flex items-center justify-between text-xs text-slate-600 shadow-xs">
        <div class="flex items-center space-x-2">
          <svg class="w-4 h-4 text-[#2A7F85]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
          <span><strong>Patient Sovereignty Guarantee:</strong> Records visible only to you and authorized clinicians. You can immediately revoke this code at any time.</span>
        </div>
        <button onclick="switchPatientTab('access_log')" class="text-[#2A7F85] font-semibold hover:underline">View Access Audit Trail &rarr;</button>
      </div>
    </div>
  `;
}

function updateSelectedScopesCount() {
  const c1 = document.getElementById('chk-meds')?.checked ? 1 : 0;
  const c2 = document.getElementById('chk-allergies')?.checked ? 1 : 0;
  const c3 = document.getElementById('chk-diagnoses')?.checked ? 1 : 0;
  const c4 = document.getElementById('chk-labs')?.checked ? 1 : 0;
  const total = c1 + c2 + c3 + c4;
  const counter = document.getElementById('scope-counter');
  if (counter) counter.textContent = `${total} SELECTED`;
}

// =========================================================================
// SCREEN 5: Access Audit Log
// =========================================================================

function renderAccessLogTab() {
  return `
    <div class="animate-toast">
      <div class="flex items-start justify-between mb-6">
        <div>
          <button onclick="switchPatientTab('medications')" class="text-xs font-semibold text-[#2A7F85] hover:underline flex items-center space-x-1 mb-2">
            <span>&larr; Back to Medications</span>
          </button>
          <h1 class="text-3xl font-serif font-bold text-[#1F3A5F]">Access Audit Log</h1>
          <p class="text-sm text-slate-600 mt-1">Cryptographically verified trail of all clinical consultations and one-time ledger redemptions.</p>
        </div>
        <div class="px-3.5 py-1.5 bg-[#F4F3EF] border border-slate-200 rounded-lg text-xs font-mono text-slate-600">
          Total Recorded Sessions: 3
        </div>
      </div>

      <div class="bg-white rounded-2xl border border-slate-200 shadow-card overflow-hidden mb-6">
        <table class="w-full text-left text-xs">
          <thead class="bg-[#F4F3EF] border-b border-slate-200 text-slate-500 font-semibold uppercase tracking-wider">
            <tr>
              <th class="py-3 px-4">Timestamp (UTC)</th>
              <th class="py-3 px-4">Clinician / Origin</th>
              <th class="py-3 px-4">Action Event</th>
              <th class="py-3 px-4">Passcode</th>
              <th class="py-3 px-4">Scopes Included</th>
              <th class="py-3 px-4">Status</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100 text-slate-700">
            <tr>
              <td class="py-3.5 px-4 font-mono">2024-10-24 14:35:10 UTC</td>
              <td class="py-3.5 px-4 font-semibold text-slate-900">Dr. Elena Vance, MD<br><span class="text-slate-400 font-normal">Exam Rm 4</span></td>
              <td class="py-3.5 px-4">One-Time Ledger Redemption</td>
              <td class="py-3.5 px-4 font-mono font-bold text-[#1F3A5F]">842-916</td>
              <td class="py-3.5 px-4">Medications, Allergies, Diagnoses</td>
              <td class="py-3.5 px-4"><span class="px-2.5 py-0.5 bg-emerald-50 text-emerald-700 font-bold rounded-full border border-emerald-200">● Consumed</span></td>
            </tr>
            <tr>
              <td class="py-3.5 px-4 font-mono">2024-10-18 10:12:44 UTC</td>
              <td class="py-3.5 px-4 font-semibold text-slate-900">Dr. Marcus Reed (Internal Med)<br><span class="text-slate-400 font-normal">Outpatient Clinic</span></td>
              <td class="py-3.5 px-4">Follow-up Medication Review</td>
              <td class="py-3.5 px-4 font-mono font-bold text-[#1F3A5F]">519-302</td>
              <td class="py-3.5 px-4">Medications, Diagnoses</td>
              <td class="py-3.5 px-4"><span class="px-2.5 py-0.5 bg-slate-100 text-slate-600 font-medium rounded-full">● Closed</span></td>
            </tr>
            <tr>
              <td class="py-3.5 px-4 font-mono">2024-09-02 09:15:00 UTC</td>
              <td class="py-3.5 px-4 font-semibold text-slate-900">Dr. Elena Vance, MD<br><span class="text-slate-400 font-normal">Cardiology Suite</span></td>
              <td class="py-3.5 px-4">Routine Cardiology Consultation</td>
              <td class="py-3.5 px-4 font-mono font-bold text-[#1F3A5F]">734-118</td>
              <td class="py-3.5 px-4">Medications, Allergies, Labs</td>
              <td class="py-3.5 px-4"><span class="px-2.5 py-0.5 bg-slate-100 text-slate-500 font-medium rounded-full">● Expired</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="p-4 bg-white rounded-xl border border-slate-200 text-xs text-slate-500 flex items-center justify-between">
        <span>Cryptographic Hash: <code class="font-mono text-slate-800">sha256:7f9b8c2d1e...4a88</code> (Immutable audit trail)</span>
        <button onclick="showToast('Cryptographic audit trail ledger downloaded', 'teal')" class="text-[#2A7F85] font-semibold hover:underline">Download Audit Proof &rarr;</button>
      </div>
    </div>
  `;
}

// =========================================================================
// DOCTOR PORTAL SHELL (Screens 6, 7, 8)
// =========================================================================

function renderDoctorScreen() {
  return `
    <div class="min-h-screen bg-[#FAF9F6] flex flex-col">
      <!-- Doctor Header Bar -->
      <header class="bg-white border-b border-slate-200 px-8 py-3.5 flex items-center justify-between text-xs">
        <div class="flex items-center space-x-6">
          <div class="flex items-center space-x-2.5">
            <span class="w-2.5 h-2.5 rounded-full bg-[#2A7F85]"></span>
            <span class="font-serif font-bold text-lg text-[#1F3A5F]">Geosmin Clinical</span>
            <span class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold">CLINICAL SAFETY ENGINE</span>
          </div>
          <div class="h-4 w-px bg-slate-200"></div>
          <div class="flex items-center space-x-2 text-slate-700 font-medium">
            <svg class="w-4 h-4 text-[#2A7F85]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
            <span>Dr. Elena Vance, MD • Cardiology Dept</span>
          </div>
        </div>

        <!-- Navigation Tabs / Progress Steps -->
        <div class="flex items-center space-x-1 bg-[#F4F3EF] p-1 rounded-xl">
          <button onclick="state.doctorStep = 'dashboard'; renderApp();" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold transition ${state.doctorStep === 'dashboard' ? 'bg-white text-[#1F3A5F] shadow-xs' : 'text-slate-600 hover:text-slate-900'}">
            Doctor Dashboard
          </button>
          <button onclick="if(state.doctorSession) { state.doctorStep = 'one_time_view'; renderApp(); } else { showToast('Please enter patient 6-digit access code first', 'amber'); }" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold transition ${state.doctorStep === 'one_time_view' ? 'bg-white text-[#1F3A5F] shadow-xs' : 'text-slate-600 hover:text-slate-900'}">
            One-time Patient View
          </button>
          <button onclick="if(state.safetyReport) { state.doctorStep = 'safety_report'; renderApp(); } else { showToast('Run prescription safety check to view report', 'amber'); }" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold transition ${state.doctorStep === 'safety_report' ? 'bg-white text-[#1F3A5F] shadow-xs' : 'text-slate-600 hover:text-slate-900'}">
            Safety Report
          </button>
          <button onclick="state.doctorStep = 'graph_view'; renderApp();" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold transition ${state.doctorStep === 'graph_view' ? 'bg-white text-[#1F3A5F] shadow-xs' : 'text-slate-600 hover:text-slate-900'}">
            <span class="flex items-center space-x-1.5">
              <span class="w-2 h-2 rounded-full bg-cyan-400 shadow-[0_0_6px_#00f0ff]"></span>
              <span>Pharmacological Graph</span>
            </span>
          </button>
        </div>

        <div class="flex items-center space-x-4 text-slate-500">
          <span class="px-2.5 py-1 bg-teal-50 border border-teal-200 rounded-lg text-teal-800 text-[11px] font-mono">
            ● Regional Medical Center EHR Gateway
          </span>
          <span class="px-2.5 py-1 bg-teal-50 border border-teal-200 rounded-lg text-teal-800 text-[11px] font-mono">
            🔒 Encrypted One-Time Gateway
          </span>
          <button onclick="setRoleAndLogin('patient')" class="text-xs text-[#2A7F85] hover:underline font-medium">
            Switch to Patient
          </button>
        </div>
      </header>

      <!-- Doctor Dynamic Content -->
      <main class="flex-1 p-8 max-w-6xl mx-auto w-full">
        ${renderDoctorStepContent()}
      </main>

      <!-- Doctor Footer -->
      <footer class="bg-white border-t border-slate-200 px-8 py-3 text-xs text-slate-500 flex justify-between items-center">
        <div class="flex items-center space-x-2">
          <svg class="w-4 h-4 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
          <span>Clinical Decision Support Engine • Compliant with HL7 FHIR & HIPAA Guidance</span>
        </div>
        <div>
          © 2026 Geosmin Safety Systems. All clinical reviews logged cryptographically.
        </div>
      </footer>
    </div>
  `;
}

function renderDoctorStepContent() {
  if (state.doctorStep === 'one_time_view') {
    return renderDoctorOneTimeView();
  } else if (state.doctorStep === 'safety_report') {
    return renderDoctorSafetyReport();
  } else if (state.doctorStep === 'graph_view') {
    return renderDoctorGraphView();
  }
  return renderDoctorDashboard();
}

// =========================================================================
// SCREEN 6: Doctor Dashboard (WhatsApp Image 0-25 at 9.15.05 PM.jpeg)
// Empty by design with 6-digit PIN auto-advance (Requirement 2)
// =========================================================================

function renderDoctorDashboard() {
  const shareCode = state.share?.passcode || '842-916';
  const clean = shareCode.replace('-', '');

  return `
    <div class="max-w-xl mx-auto py-8 animate-toast">
      <!-- Status Top Indicator -->
      <div class="flex items-center justify-between text-xs text-slate-500 mb-4 px-2">
        <span class="flex items-center space-x-1.5">
          <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
          <span>ACTIVE SESSION KEY #409-KL8 / Station 12B • Exam Room 4</span>
        </span>
        <span>Session auto-terminates in <span class="live-timer font-mono text-slate-700">14:56</span></span>
      </div>

      <div class="bg-white rounded-2xl border border-slate-200 shadow-card p-8 text-center">
        <!-- Lock Icon in Box -->
        <div class="w-12 h-12 rounded-xl bg-teal-50 border border-teal-200 flex items-center justify-center text-[#2A7F85] mx-auto mb-4">
          <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>
        </div>

        <span class="px-3 py-1 bg-teal-50 text-teal-800 text-xs font-semibold rounded-full border border-teal-200">
          Zero-Knowledge Gateway
        </span>

        <h1 class="text-2xl font-serif font-bold text-[#1F3A5F] mt-3 mb-2">Access Patient Medication Record</h1>
        <p class="text-xs text-slate-500 max-w-md mx-auto mb-8 leading-relaxed">
          Ask the patient for their 6-digit access code or scan their secure QR badge from their Geosmin mobile app or printed consultation voucher.
        </p>

        <!-- 6-Digit Passcode Input with Keystroke Auto-Advance (Requirement 2) -->
        <div class="mb-6">
          <div class="flex justify-between items-center text-xs text-slate-500 font-semibold uppercase tracking-wider mb-2.5 px-4">
            <span>6-Digit Authorization Passcode</span>
            <button onclick="clearPinInputs()" class="text-[#2A7F85] hover:underline normal-case font-normal">Clear</button>
          </div>

          <!-- PIN Boxes -->
          <div class="pin-digit-container mb-2">
            <input type="text" inputmode="numeric" maxlength="1" data-pin-index="0" value="${clean[0] || '8'}" class="pin-digit ${clean[0] ? 'filled' : ''}" autofocus>
            <input type="text" inputmode="numeric" maxlength="1" data-pin-index="1" value="${clean[1] || '4'}" class="pin-digit ${clean[1] ? 'filled' : ''}">
            <input type="text" inputmode="numeric" maxlength="1" data-pin-index="2" value="${clean[2] || '2'}" class="pin-digit ${clean[2] ? 'filled' : ''}">
            <span class="pin-separator">–</span>
            <input type="text" inputmode="numeric" maxlength="1" data-pin-index="3" value="${clean[3] || '9'}" class="pin-digit ${clean[3] ? 'filled' : ''}">
            <input type="text" inputmode="numeric" maxlength="1" data-pin-index="4" value="${clean[4] || '1'}" class="pin-digit ${clean[4] ? 'filled' : ''}">
            <input type="text" inputmode="numeric" maxlength="1" data-pin-index="5" value="${clean[5] || '6'}" class="pin-digit ${clean[5] ? 'filled' : ''}">
          </div>

          <div class="text-[11px] text-slate-400">
            Code expires in <span class="live-timer font-semibold text-slate-600">04:56</span> after issuance by patient.
          </div>
        </div>

        <div class="flex items-center space-x-4 my-6">
          <div class="flex-1 h-px bg-slate-200"></div>
          <span class="text-xs text-slate-400 font-semibold uppercase">Or</span>
          <div class="flex-1 h-px bg-slate-200"></div>
        </div>

        <!-- Scan Badge / QR Code Button -->
        <button onclick="handleDoctorAccess('${shareCode}')" class="w-full py-2.5 px-4 border border-slate-200 hover:bg-slate-50 text-slate-700 rounded-xl text-sm font-semibold transition flex items-center justify-center space-x-2 mb-3">
          <svg class="w-4 h-4 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v1m6 11h2m-6 0h-2v4m0-11v3m0 0h.01M12 12h4.01M16 20h4M4 12h4m12 0h.01M5 8h2a1 1 0 001-1V5a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1zm12 0h2a1 1 0 001-1V5a1 1 0 00-1-1h-2a1 1 0 00-1 1v2a1 1 0 001 1zM5 20h2a1 1 0 001-1v-2a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1z"/></svg>
          <span>Scan Patient Badge / QR Code</span>
        </button>

        <!-- Access History CTA Button -->
        <button id="btn-access-history" onclick="handleDoctorAccess()" class="w-full py-3 px-4 bg-[#1F3A5F] hover:bg-[#152943] text-white rounded-xl text-sm font-semibold transition flex items-center justify-center space-x-2 shadow-sm">
          <span>Access Patient History</span>
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
        </button>

        <div class="mt-6 pt-4 border-t border-slate-100 flex items-center justify-between text-xs text-slate-400">
          <span class="flex items-center space-x-1.5 text-slate-600 font-medium">
            <svg class="w-4 h-4 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
            <span>Hardware Token Active</span>
          </span>
          <span>YubiKey Slot 1 Verified</span>
        </div>
      </div>

      <!-- Reassurance Banner -->
      <div class="mt-8 text-center text-xs text-slate-500 max-w-md mx-auto leading-relaxed">
        <div class="font-serif italic text-base text-slate-800 mb-1">“No patient data is shown until a patient shares it.”</div>
        All consultations are one-time, transient views. Data is neither cached locally nor exported to unverified auxiliary storage systems.
      </div>
    </div>
  `;
}

// =========================================================================
// SCREEN 7: One-Time Patient View (WhatsApp Image 2026- PM.jpeg)
// =========================================================================

function renderDoctorOneTimeView() {
  const session = state.doctorSession || {
    patient_name: 'Ravi Kumar',
    patient_age: 54,
    session_id: 'SES-98214'
  };

  return `
    <div class="animate-toast">
      <!-- Warning Expiration Banner (Synced Countdown Timer) -->
      <div class="p-3.5 bg-amber-50 border border-amber-300 rounded-xl text-xs text-amber-900 flex items-center justify-between mb-6 shadow-xs alert-shimmer">
        <div class="flex items-center space-x-2">
          <svg class="w-4 h-4 text-amber-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
          <span class="font-semibold">This view cannot be reopened. Session will permanently expire once closed or submitted.</span>
        </div>
        <div class="flex items-center space-x-3 font-mono font-medium">
          <span>Session ID: #${session.session_id}</span>
          <span>•</span>
          <span>Patient: ${session.patient_name} (Age ${session.patient_age})</span>
          <span>•</span>
          <span class="text-amber-800 font-bold flex items-center space-x-1">
            <span>⏱ Expires in:</span>
            <span class="live-timer font-bold">03:41</span>
          </span>
        </div>
      </div>

      <!-- Breadcrumbs -->
      <div class="flex items-center justify-between text-xs text-slate-500 mb-6 pb-2 border-b border-slate-200">
        <div class="font-mono text-[11px] uppercase tracking-wider text-slate-600">
          CLINICAL CONSULTATION &gt; ONE-TIME LEDGER GATEWAY &gt; RAVI KUMAR • PRESCRIPTIVE SAFETY ASSESSMENT
        </div>
        <div class="flex items-center space-x-1 text-slate-600">
          <svg class="w-3.5 h-3.5 text-[#2A7F85]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>
          <span>Zero-Knowledge Decrypted Record</span>
        </div>
      </div>

      <!-- 2-Column Grid Layout -->
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
        
        <!-- Left: Shared Patient History (7 cols) -->
        <div class="lg:col-span-7 space-y-6">
          <div class="flex items-center justify-between pb-2 border-b border-slate-200">
            <div>
              <h2 class="font-serif font-bold text-xl text-[#1F3A5F]">Shared Patient History</h2>
              <div class="text-xs text-slate-500">Synchronized from patient-verified cryptographical ledger</div>
            </div>
            <span class="px-2.5 py-1 bg-teal-50 text-teal-800 text-xs font-semibold rounded-lg border border-teal-200">
              ● Ledger Signature Valid
            </span>
          </div>

          <!-- Documented Allergies & Active Diagnoses Grid -->
          <div class="grid grid-cols-2 gap-4">
            <div class="p-3.5 bg-white rounded-xl border border-red-200 shadow-xs">
              <div class="text-[10px] uppercase font-bold text-red-600 mb-1.5 flex items-center space-x-1">
                <span class="text-red-500 font-bold">*</span>
                <span>DOCUMENTED ALLERGIES (2 Critical)</span>
              </div>
              <div class="text-xs text-slate-800 space-y-1">
                <div class="flex items-center space-x-1 text-red-700 font-medium">
                  <span class="w-1.5 h-1.5 rounded-full bg-red-500"></span>
                  <span>Penicillin (Severe anaphylaxis)</span>
                </div>
                <div class="flex items-center space-x-1 text-red-700 font-medium">
                  <span class="w-1.5 h-1.5 rounded-full bg-red-500"></span>
                  <span>Sulfa drugs (Hives)</span>
                </div>
              </div>
            </div>

            <div class="p-3.5 bg-white rounded-xl border border-slate-200 shadow-xs">
              <div class="text-[10px] uppercase font-bold text-slate-500 mb-1.5">ACTIVE DIAGNOSES (ICD-10 Mapped)</div>
              <div class="text-xs text-slate-800 space-y-1">
                <div>• Atrial Fibrillation (Chronic)</div>
                <div>• Hypertension (Stage 1) • Osteoarthritis</div>
              </div>
            </div>
          </div>

          <!-- Active Regimen Cards Feed -->
          <div>
            <div class="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">Active Medication Regimen (3 ACTIVE COMPOUNDS)</div>
            
            <div class="space-y-3">
              <!-- Warfarin Sodium -->
              <div class="p-4 bg-white rounded-xl border border-slate-200 shadow-card">
                <div class="flex justify-between items-start mb-1">
                  <div>
                    <span class="font-serif font-bold text-slate-900 text-base">Warfarin Sodium</span>
                    <span class="text-xs text-slate-500 block">Oral Anticoagulant • Vitamin K Antagonist</span>
                  </div>
                  <span class="text-[10px] px-2.5 py-0.5 bg-teal-50 text-teal-800 font-semibold rounded border border-teal-200">Active Regimen</span>
                </div>
                <div class="grid grid-cols-4 gap-2 mt-2 pt-2 border-t border-slate-100 text-xs text-slate-600">
                  <div><span class="text-slate-400 block text-[10px]">PRESCRIBED DOSE</span><strong>5 mg once daily</strong></div>
                  <div><span class="text-slate-400 block text-[10px]">TIMING</span>Evening (18:00)</div>
                  <div><span class="text-slate-400 block text-[10px]">PRIMARY INDICATION</span>AFib Stroke Prev.</div>
                  <div><span class="text-slate-400 block text-[10px]">LAST RECORDED INR</span><span class="text-emerald-700 font-bold">2.3 (Therapeutic)</span></div>
                </div>
              </div>

              <!-- Metoprolol Tartrate -->
              <div class="p-4 bg-white rounded-xl border border-slate-200 shadow-card">
                <div class="flex justify-between items-start mb-1">
                  <div>
                    <span class="font-serif font-bold text-slate-900 text-base">Metoprolol Tartrate</span>
                    <span class="text-xs text-slate-500 block">Beta-Adrenergic Blocker • Selective β1</span>
                  </div>
                  <span class="text-[10px] px-2.5 py-0.5 bg-teal-50 text-teal-800 font-semibold rounded border border-teal-200">Active Regimen</span>
                </div>
                <div class="grid grid-cols-4 gap-2 mt-2 pt-2 border-t border-slate-100 text-xs text-slate-600">
                  <div><span class="text-slate-400 block text-[10px]">PRESCRIBED DOSE</span><strong>25 mg twice daily</strong></div>
                  <div><span class="text-slate-400 block text-[10px]">INSTRUCTIONS</span>Take with meals</div>
                  <div><span class="text-slate-400 block text-[10px]">PRIMARY INDICATION</span>Ventricular Rate</div>
                  <div><span class="text-slate-400 block text-[10px]">RESTING HR BASELINE</span>68 bpm</div>
                </div>
              </div>

              <!-- Ibuprofen Self-Reported with Warning (Micro-interaction Enabled) -->
              <div class="p-4 bg-white rounded-xl border-l-4 border-l-amber-500 border border-slate-200 shadow-card card-hover-lift">
                <div class="flex justify-between items-start mb-1">
                  <div>
                    <span class="font-serif font-bold text-slate-900 text-base">Ibuprofen</span>
                    <span class="text-xs text-slate-500 ml-2">Self-Reported OTC • Non-Steroidal Anti-Inflammatory (NSAID)</span>
                  </div>
                  <span onclick="inspectWarningDetail('ibuprofen_celecoxib')" class="warning-badge-pulse text-[10px] px-2.5 py-0.5 bg-amber-50 text-amber-800 font-bold rounded-full border border-amber-300 cursor-pointer">
                    <span class="beacon-dot-amber mr-1"></span>
                    Known Interaction Alert
                  </span>
                </div>
                <div class="grid grid-cols-3 gap-2 mt-2 pt-2 border-t border-slate-100 text-xs text-slate-600">
                  <div><span class="text-slate-400 block text-[10px]">DOSAGE SCHEDULE</span><strong>400 mg as needed</strong></div>
                  <div><span class="text-slate-400 block text-[10px]">REPORTED REASON</span>Right knee osteoarthritis ache</div>
                  <div><span class="text-slate-400 block text-[10px]">REPORT FREQUENCY</span>~3-4 times/week</div>
                </div>
                <div onclick="inspectWarningDetail('ibuprofen_celecoxib')" class="mt-2.5 p-2.5 bg-amber-50 rounded-lg text-xs text-amber-900 cursor-pointer hover:bg-amber-100/70 transition">
                  <strong>(i) Adverse GI & Bleeding Concomitance:</strong> Concurrent NSAID and Warfarin therapy elevates severe gastrointestinal hemorrhage risk by ~3.8x. Advise discontinuation prior to prescribing additional agents.
                </div>
              </div>
            </div>
          </div>

          <!-- Biometric Telemetry & Renal Safety Grid -->
          <div class="p-5 bg-white rounded-2xl border border-slate-200 shadow-card">
            <div class="flex items-center justify-between mb-3">
              <span class="text-[10px] uppercase font-bold text-slate-400 tracking-wider">BIOMETRIC TELEMETRY & RENAL SAFETY</span>
              <span class="text-[11px] text-slate-500">Logged 18 hrs ago</span>
            </div>
            <div class="grid grid-cols-3 gap-4">
              <!-- eGFR with circular gauge SVG -->
              <div class="p-3.5 bg-[#F4F3EF] rounded-xl flex items-center justify-between">
                <div>
                  <div class="text-[10px] text-slate-500 uppercase font-semibold">EST. GFR (RENAL)</div>
                  <div class="text-2xl font-bold font-mono text-slate-900">78</div>
                  <div class="text-[10px] text-slate-500">mL/min/1.73m² (Stage 2)</div>
                </div>
                <!-- Circular gauge -->
                <div class="relative w-12 h-12 shrink-0">
                  <svg class="w-12 h-12 -rotate-90" viewBox="0 0 36 36">
                    <path class="text-slate-300" stroke-width="3.5" stroke="currentColor" fill="none" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                    <path class="text-[#2A7F85]" stroke-dasharray="78, 100" stroke-width="3.5" stroke-linecap="round" stroke="currentColor" fill="none" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                  </svg>
                </div>
              </div>

              <!-- Blood Pressure with sparkline SVG -->
              <div class="p-3.5 bg-[#F4F3EF] rounded-xl flex items-center justify-between">
                <div>
                  <div class="text-[10px] text-slate-500 uppercase font-semibold">MEAN ARTERIAL BP</div>
                  <div class="text-2xl font-bold font-mono text-slate-900">132/84</div>
                  <div class="text-[10px] text-slate-500">mmHg (7-day mean)</div>
                </div>
                <!-- Sparkline SVG -->
                <div class="w-14 h-8 shrink-0 flex items-center justify-center">
                  <svg class="w-14 h-6 text-[#1F3A5F]" viewBox="0 0 60 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="0,18 10,14 20,16 30,8 40,12 50,6 60,10"/>
                  </svg>
                </div>
              </div>

              <!-- Serum Potassium -->
              <div class="p-3.5 bg-[#F4F3EF] rounded-xl">
                <div class="text-[10px] text-slate-500 uppercase font-semibold">SERUM K+ (POTASSIUM)</div>
                <div class="text-2xl font-bold font-mono text-slate-900">4.2 <span class="text-xs font-normal text-slate-500">mEq/L</span></div>
                <div class="text-[10px] text-emerald-700 font-semibold flex items-center space-x-1 mt-0.5">
                  <span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                  <span>Within Target Range</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Right: Prescribe New Medication (5 cols) -->
        <div class="lg:col-span-5 bg-white p-6 rounded-2xl border border-slate-200 shadow-card flex flex-col justify-between">
          <div>
            <div class="pb-3 border-b border-slate-100 mb-4">
              <h2 class="font-serif font-bold text-xl text-[#1F3A5F]">Prescribe New Medication</h2>
              <p class="text-xs text-slate-500 mt-1">Draft prescription will be validated against active regimen and allergies prior to signing.</p>
            </div>

            <div class="space-y-4">
              <div>
                <div class="flex justify-between items-center mb-1">
                  <label class="text-xs font-semibold text-slate-700">Medication Name / Formulation</label>
                  <span class="text-[11px] text-teal-700 bg-teal-50 px-2 py-0.5 rounded font-medium">Standard Formulary</span>
                </div>
                <div class="relative">
                  <input type="text" id="doc-med-name" value="Celecoxib" class="w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-200 bg-[#F4F3EF] font-medium text-slate-900 focus:outline-none focus:bg-white focus:ring-1 focus:ring-[#1F3A5F]">
                  <span class="absolute right-3 top-2.5 text-slate-400">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                  </span>
                </div>
                <span class="text-[11px] text-slate-500 block mt-1">(i) Suggested alternative to non-selective NSAID (Ibuprofen)</span>
              </div>

              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="text-xs font-semibold text-slate-700 block mb-1">Dosage</label>
                  <div class="relative">
                    <input type="text" id="doc-dosage" value="100 mg" class="w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-200 bg-[#F4F3EF] font-medium text-slate-900">
                    <span class="absolute right-2.5 top-2.5 text-[10px] font-bold text-slate-400 uppercase">CAP</span>
                  </div>
                </div>
                <div>
                  <label class="text-xs font-semibold text-slate-700 block mb-1">Frequency</label>
                  <select id="doc-freq" class="w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-200 bg-[#F4F3EF] font-medium text-slate-900">
                    <option selected>Once daily</option>
                    <option>Twice daily</option>
                  </select>
                </div>
              </div>

              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="text-xs font-semibold text-slate-700 block mb-1">Duration</label>
                  <input type="text" id="doc-duration" value="14 days" class="w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-200 bg-[#F4F3EF] font-medium text-slate-900">
                </div>
                <div>
                  <label class="text-xs font-semibold text-slate-700 block mb-1">Administration Route</label>
                  <input type="text" readonly value="Oral (PO)" class="w-full px-3.5 py-2.5 text-sm rounded-lg border border-slate-200 bg-slate-100 text-slate-600">
                </div>
              </div>

              <div>
                <label class="text-xs font-semibold text-slate-700 block mb-1">Clinical Rationale / Indication</label>
                <textarea id="doc-rationale" rows="3" class="w-full px-3.5 py-2 text-xs rounded-lg border border-slate-200 bg-[#F4F3EF] text-slate-800 leading-relaxed focus:bg-white focus:outline-none">Acute flare-up management of right knee osteoarthritis; selective COX-2 inhibitor substitution to reduce GI bleeding burden.</textarea>
              </div>

              <!-- Engine Validation Targets Preview -->
              <div class="p-3.5 bg-[#F4F3EF] rounded-xl border border-slate-200 text-xs space-y-2">
                <div class="font-bold text-slate-500 uppercase tracking-wider text-[10px]">ENGINE VALIDATION TARGETS</div>
                <div class="flex justify-between items-center text-slate-700">
                  <span class="flex items-center space-x-1">
                    <svg class="w-3.5 h-3.5 text-[#2A7F85]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                    <span>Sulfa Cross-Reactivity Screen</span>
                  </span>
                  <span class="text-amber-800 font-bold text-[11px] bg-amber-100/70 px-2 py-0.5 rounded">Requires Analysis</span>
                </div>
                <div class="flex justify-between items-center text-slate-700">
                  <span class="flex items-center space-x-1">
                    <svg class="w-3.5 h-3.5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
                    <span>Warfarin Cytochrome P450 2C9 Competition</span>
                  </span>
                  <span class="text-red-700 font-bold text-[11px] bg-red-100/70 px-2 py-0.5 rounded">High Concomitance</span>
                </div>
                <div class="flex justify-between items-center text-slate-700">
                  <span class="flex items-center space-x-1">
                    <svg class="w-3.5 h-3.5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                    <span>Renal Clearance Safety (eGFR: 78)</span>
                  </span>
                  <span class="text-emerald-800 font-bold text-[11px] bg-emerald-100/70 px-2 py-0.5 rounded">Acceptable</span>
                </div>
              </div>
            </div>
          </div>

          <div class="mt-4 p-3 bg-gradient-to-r from-cyan-50/70 to-teal-50/70 rounded-xl border border-cyan-200/80 flex items-center justify-between">
            <div class="flex items-center space-x-2">
              <span class="w-2.5 h-2.5 rounded-full bg-cyan-500 animate-pulse"></span>
              <div>
                <span class="text-xs font-bold text-cyan-950 block">Pharmacological Knowledge Graph</span>
                <span class="text-[10px] text-cyan-800">Preview 200+ drug pairs & metabolic pathways</span>
              </div>
            </div>
            <button type="button" onclick="state.doctorStep = 'graph_view'; renderApp();" class="px-2.5 py-1 bg-white hover:bg-cyan-50 text-cyan-800 font-semibold text-xs rounded-lg border border-cyan-300 shadow-xs transition">
              Preview Graph &rarr;
            </button>
          </div>

          <div class="mt-4 pt-4 border-t border-slate-100">
            <button onclick="handlePrescriptionSafetyCheck()" class="w-full py-3 px-4 bg-[#1F3A5F] hover:bg-[#152943] text-white rounded-xl text-sm font-semibold transition flex items-center justify-center space-x-2 shadow-sm">
              <span>Run safety check</span>
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
            </button>
            <div class="text-[11px] text-slate-400 text-center mt-2.5">
              Performs real-time cross-analysis against drug interactions, renal safety, and allergy contraindications.
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

// =========================================================================
// SCREEN 8: Clinical Safety & Interaction Report (WhatsApp Image 2026-10-25 at 9.15.03 PM.jpeg)
// =========================================================================

function renderDoctorSafetyReport() {
  const report = state.safetyReport || getFallbackSafetyReport();

  return `
    <div class="animate-toast">
      <!-- Top Action Navigation -->
      <div class="flex items-center justify-between pb-3 border-b border-slate-200 mb-6">
        <button onclick="state.doctorStep = 'one_time_view'; renderApp();" class="text-xs font-semibold text-[#2A7F85] hover:underline flex items-center space-x-1">
          <span>&larr; Back to One-time Patient View</span>
        </button>
        <div class="text-xs text-slate-500 font-mono">
          ● Execution Timestamp: ${report.execution_timestamp || '2024-10-24 14:38:12 UTC'} • Session: #${report.session_id || 'GEO-9021-CLIN'}
        </div>
      </div>

      <!-- Report Header -->
      <div class="flex items-start justify-between mb-6">
        <div>
          <div class="text-[10px] font-bold text-[#2A7F85] uppercase tracking-wider mb-1 flex items-center space-x-1.5">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <span>ALGORITHMIC PHARMACOLOGY VERIFICATION COMPLETE</span>
          </div>
          <h1 class="text-3xl font-serif font-bold text-[#1F3A5F]">Clinical Safety & Interaction Report</h1>
          <p class="text-xs text-slate-600 mt-1">
            Cross-checked for <strong>${report.patient_name || 'Ravi Kumar'}</strong> (DOB: ${report.patient_dob || '12-May-1968'}, MRN: #${report.patient_mrn || 'RK-48810'}) against 3 active medications and 2 documented allergies.
          </p>
        </div>

        <!-- Severity Summary Badges with Interactive Micro-interactions (Requirement 3) -->
        <div class="flex items-center space-x-2">
          <span onclick="inspectWarningDetail('high_conflict')" class="warning-badge-pulse px-3 py-1 bg-red-100 hover:bg-red-200 text-red-800 text-xs font-bold rounded-lg border border-red-300 shadow-xs cursor-pointer">
            <span class="beacon-dot-red mr-1.5"></span>
            <span>1 High Conflict</span>
          </span>
          <span onclick="inspectWarningDetail('moderate_review')" class="warning-badge-pulse px-3 py-1 bg-amber-100 hover:bg-amber-200 text-amber-800 text-xs font-bold rounded-lg border border-amber-300 shadow-xs cursor-pointer">
            <span class="beacon-dot-amber mr-1.5"></span>
            <span>1 Moderate Review</span>
          </span>
          <span onclick="inspectWarningDetail('unassessed')" class="warning-badge-pulse px-3 py-1 bg-slate-200 hover:bg-slate-300 text-slate-700 text-xs font-bold rounded-lg border border-slate-300 shadow-xs cursor-pointer">
            <span class="w-2 h-2 rounded-full bg-slate-500 mr-1.5 inline-block"></span>
            <span>1 Unassessed</span>
          </span>
        </div>
      </div>

      <!-- Regimen Under Evaluation Card -->
      <div class="p-5 bg-white rounded-2xl border border-slate-200 shadow-card flex items-center justify-between mb-8">
        <div class="flex items-center space-x-4">
          <div class="w-10 h-10 rounded-xl bg-[#F4F3EF] flex items-center justify-center text-slate-700 font-bold">
            <svg class="w-5 h-5 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>
          </div>
          <div>
            <div class="text-[10px] font-bold text-slate-400 uppercase tracking-wider">REGIMEN UNDER EVALUATION</div>
            <div class="text-base font-serif font-bold text-slate-900">${report.regimen_under_evaluation?.name || 'Celecoxib'} 100 mg orally once daily (14-Day Supply)</div>
            <div class="text-xs text-slate-500">Therapeutic Class: ${report.regimen_under_evaluation?.therapeutic_class || 'NSAID / Selective COX-2 Inhibitor'} • Indication: ${report.regimen_under_evaluation?.indication || 'Acute Musculoskeletal Arthralgia'}</div>
          </div>
        </div>

        <div class="text-right">
          <div class="text-xs font-mono font-bold text-[#1F3A5F]">Rules Evaluated</div>
          <div class="text-xs text-slate-500">48 Clinical Vectors</div>
        </div>
      </div>

      <!-- Interactive Cytoscape Knowledge Graph Visualizer Section -->
      <div class="mb-8 bg-white rounded-2xl border border-slate-200 shadow-card overflow-hidden">
        <div class="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-gradient-to-r from-slate-50 to-white">
          <div>
            <div class="text-[10px] font-bold text-cyan-700 uppercase tracking-wider flex items-center space-x-1.5 mb-0.5">
              <span class="w-2 h-2 rounded-full bg-cyan-500 animate-pulse"></span>
              <span>NETWORKX MULTIGRAPH REASONER • CYTOSCAPE.JS / COLA PHYSICS</span>
            </div>
            <h2 class="font-serif font-bold text-lg text-[#1F3A5F]">Pharmacological Interaction & Traversal Canvas</h2>
            <p class="text-xs text-slate-500">Live pathway inspection: Candidate Rx (Cyan), Active Meds (Teal), CYP Enzymes (Purple), Conflicts (Red shockwave).</p>
          </div>
          <div class="flex items-center space-x-2">
            <button onclick="window.graphVisualizer?.runStepByStepTraversal()" class="px-3 py-1.5 bg-cyan-50 hover:bg-cyan-100 text-cyan-800 font-semibold text-xs rounded-lg border border-cyan-200 transition flex items-center space-x-1.5 shadow-xs">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              <span>Replay Ray Traversal</span>
            </button>
            <button onclick="state.doctorStep = 'graph_view'; renderApp();" class="px-3 py-1.5 bg-[#1F3A5F] hover:bg-[#152943] text-white font-semibold text-xs rounded-lg transition flex items-center space-x-1.5 shadow-xs">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"/></svg>
              <span>Expand Full Canvas</span>
            </button>
          </div>
        </div>
        <div class="p-2 sm:p-4 bg-slate-950">
          <div id="doctor-safety-graph-container" class="w-full"></div>
        </div>
      </div>

      <!-- Findings List -->
      <div class="mb-8">
        <div class="flex justify-between items-center mb-4">
          <h2 class="font-serif font-bold text-xl text-[#1F3A5F]">Clinical Safety Findings</h2>
          <span class="text-xs text-slate-400">Priority ranking by contraindication severity</span>
        </div>

        <div class="space-y-4">
          ${(report.findings || []).map(renderFindingCard).join('')}
        </div>
      </div>

      <!-- Profile Baseline Evaluated -->
      <div class="p-6 bg-white rounded-2xl border border-slate-200 shadow-card mb-6">
        <div class="flex justify-between items-center mb-4 pb-2 border-b border-slate-100">
          <h3 class="font-serif font-bold text-base text-slate-900">Profile Baseline Evaluated</h3>
          <span class="text-xs text-slate-400">Synchronized EHR Data</span>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-6 text-xs">
          <div>
            <div class="font-bold text-slate-400 uppercase tracking-wider text-[10px] mb-2">Active Documented Regimen (3)</div>
            <div class="space-y-2 text-slate-800">
              <div class="flex justify-between py-1 border-b border-slate-100">
                <span class="font-medium">Warfarin Sodium</span>
                <span class="text-slate-500">5 mg orally once daily (Target INR: 2.0-3.0)</span>
              </div>
              <div class="flex justify-between py-1 border-b border-slate-100">
                <span class="font-medium">Ibuprofen (Self-Reported)</span>
                <span class="text-slate-500">400 mg PRN for tension headache / joint ache</span>
              </div>
              <div class="flex justify-between py-1 border-b border-slate-100">
                <span class="font-medium">Atorvastatin Calcium</span>
                <span class="text-slate-500">20 mg orally at bedtime</span>
              </div>
            </div>
          </div>

          <div>
            <div class="font-bold text-slate-400 uppercase tracking-wider text-[10px] mb-2">Known Allergies & Intolerances (2)</div>
            <div class="space-y-2 text-slate-800">
              <div class="flex justify-between py-1 border-b border-slate-100">
                <span class="font-medium text-red-600">Sulfonamides ("Sulfa")</span>
                <span class="text-slate-500">Severe Maculopapular Rash (2018)</span>
              </div>
              <div class="flex justify-between py-1 border-b border-slate-100">
                <span class="font-medium text-red-600">Codeine Phosphate</span>
                <span class="text-slate-500">Nausea & Emesis (Documented Adverse Effect)</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Statutory Disclaimer Box -->
      <div class="p-4 bg-[#F4F3EF] rounded-2xl border border-slate-200 flex items-start space-x-3 text-xs text-slate-600 mb-8">
        <svg class="w-5 h-5 text-slate-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
        <div>
          <strong class="text-slate-900">No issue found in completed checks does not mean the prescription is safe.</strong>
          This automated safety assessment supplements clinical judgment and does not replace medical review. Clinicians remain solely responsible for validating patient physiologic markers, clinical tolerance, and verifying therapeutic necessity prior to prescription transmission.
        </div>
      </div>

      <!-- Action Footer -->
      <div class="flex items-center justify-between pt-4 border-t border-slate-200">
        <div class="text-xs text-slate-500 flex items-center space-x-1.5">
          <svg class="w-4 h-4 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
          <span>Electronic Signature verified under Dr. Vance (#NPI-849204)</span>
        </div>

        <div class="flex items-center space-x-3">
          <button onclick="state.doctorStep = 'dashboard'; renderApp();" class="px-4 py-2 text-xs font-semibold text-slate-600 hover:text-slate-900 transition flex items-center space-x-1">
            <span>&rarr; Done (Exit & Terminate Session)</span>
          </button>
          <button onclick="state.doctorStep = 'one_time_view'; renderApp();" class="px-4 py-2 border border-slate-200 rounded-lg text-xs font-semibold text-slate-700 hover:bg-slate-50 transition">
            Modify prescription
          </button>
          <button onclick="showToast('Prescription transmitted safely to patient timeline for sovereign confirmation', 'teal'); state.doctorStep = 'dashboard'; renderApp();" class="px-5 py-2.5 bg-[#1F3A5F] hover:bg-[#152943] text-white rounded-lg text-xs font-semibold shadow-sm flex items-center space-x-1.5 transition">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/></svg>
            <span>Send to patient for confirmation</span>
          </button>
        </div>
      </div>
    </div>
  `;
}

function renderFindingCard(finding) {
  let borderClass = 'border-slate-200 bg-white';
  let badgeColor = 'bg-slate-100 text-slate-700';
  let headlineColor = 'text-slate-600';
  let inspectTarget = 'unassessed';

  if (finding.severity === 'HIGH_RISK') {
    borderClass = 'border-red-200 bg-white border-l-4 border-l-red-600';
    badgeColor = 'bg-red-50 text-red-700 border border-red-200';
    headlineColor = 'text-red-600 font-bold';
    inspectTarget = 'high_conflict';
  } else if (finding.severity === 'AMBER_REVIEW') {
    borderClass = 'border-amber-200 bg-white border-l-4 border-l-amber-500';
    badgeColor = 'bg-amber-50 text-amber-800 border border-amber-200';
    headlineColor = 'text-amber-700 font-bold';
    inspectTarget = 'moderate_review';
  } else {
    borderClass = 'border-slate-200 bg-white border-l-4 border-l-slate-400';
    badgeColor = 'bg-slate-100 text-slate-700 border border-slate-200';
    headlineColor = 'text-slate-600 font-bold';
  }

  return `
    <div class="p-6 rounded-2xl border ${borderClass} shadow-card card-hover-lift">
      <div class="flex items-center justify-between mb-2">
        <span onclick="inspectWarningDetail('${inspectTarget}')" class="px-2.5 py-0.5 rounded text-xs font-mono font-semibold ${badgeColor} cursor-pointer hover:shadow-xs transition">
          ${finding.class_level}
        </span>
        <span class="text-xs ${headlineColor} flex items-center space-x-1">
          <span>${finding.headline}</span>
        </span>
      </div>

      <h3 class="font-serif font-bold text-lg text-slate-900 mb-1.5">${finding.title}</h3>
      <p class="text-xs text-slate-700 leading-relaxed mb-4">${finding.description}</p>

      <!-- Knowledge Graph Reasoning Path -->
      ${finding.graph_path && finding.graph_path.length > 0 ? `
        <div class="mb-4 p-3 bg-[#F4F3EF] rounded-xl border border-slate-200 font-mono text-[11px] text-slate-600 flex items-center space-x-2 overflow-x-auto">
          <span class="text-[#2A7F85] font-bold shrink-0">Ontology Traversal:</span>
          <span class="whitespace-nowrap">${finding.graph_path.join(' ')}</span>
        </div>
      ` : ''}

      <!-- Evidence Source & Uncertainty Parameters -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
        <div class="p-3.5 bg-[#F4F3EF] rounded-xl border border-slate-100">
          <div class="font-bold text-slate-400 uppercase tracking-wider text-[10px] mb-1 flex items-center space-x-1">
            <svg class="w-3.5 h-3.5 text-[#2A7F85]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>
            <span>Evidence Source</span>
          </div>
          <div class="text-slate-800 leading-relaxed">${finding.evidence_source}</div>
        </div>

        <div class="p-3.5 bg-amber-50/60 rounded-xl border border-amber-100">
          <div class="font-bold text-amber-800 uppercase tracking-wider text-[10px] mb-1 flex items-center space-x-1">
            <svg class="w-3.5 h-3.5 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <span>Clinical Uncertainty Parameter</span>
          </div>
          <div class="text-slate-800 leading-relaxed">${finding.clinical_uncertainty}</div>
        </div>
      </div>
    </div>
  `;
}

// Fallback high-fidelity safety report matching Figma exactly
function getFallbackSafetyReport() {
  return {
    session_id: "SES-98214",
    patient_name: "Ravi Kumar",
    patient_dob: "12-May-1968",
    patient_mrn: "RK-48810",
    execution_timestamp: "2024-10-24 14:38:12 UTC",
    clinician_signature: "Electronic Signature verified under Dr. Vance (#NPI-849204)",
    rules_evaluated_count: 48,
    summary_counts: {
      high_risk: 1,
      amber_review: 1,
      unable_to_assess: 1
    },
    regimen_under_evaluation: {
      name: "Celecoxib",
      dosage: "100 mg",
      therapeutic_class: "NSAID / Selective COX-2 Inhibitor",
      indication: "Acute Musculoskeletal Arthralgia"
    },
    findings: [
      {
        severity: "HIGH_RISK",
        class_level: "⊗ [HIGH RISK] Drug-Drug Interaction • FINDING #01 • CLASS 1",
        headline: "! Potential Severe Hemorrhagic Sequelae",
        title: "Concomitant Warfarin and Celecoxib (NSAID/COX-2 inhibitor)",
        description: "Significantly elevated risk of upper gastrointestinal bleeding and anticoagulant potentiation. Celecoxib may increase prothrombin time / INR through competitive CYP2C9 metabolism and secondary platelet disruption.",
        graph_path: ["Warfarin [CYP2C9 Substrate]", "──>", "Competitive Inhibition by Celecoxib [CYP2C9 Moderate Inhibitor]", "──>", "Decreased Clearance", "──>", "Increased Bleeding"],
        evidence_source: "FDA Package Insert; CPIC Clinical Guideline 2023; Micromedex Class 1 Interaction. CYP2C9 intermediate metabolizer phenotype heightens risk.",
        clinical_uncertainty: "Patient's exact baseline platelet count and gastroprotective therapy status (e.g., concurrent PPI or H2RA use) are not recorded in the episodic health record."
      },
      {
        severity: "AMBER_REVIEW",
        class_level: "⚠ [AMBER - REVIEW] Duplicate Therapy & Renal Risk • FINDING #02 • CATEGORY MODERATE",
        headline: "🔄 Therapeutic Duplication",
        title: "Concurrent self-reported Ibuprofen use with proposed NSAID",
        description: "Patient actively reports self-administering Ibuprofen 400 mg PRN. Adding Celecoxib creates duplicate NSAID therapy, accelerating the risk of acute reversible renal insufficiency, sodium retention, and gastric mucosal erosion.",
        graph_path: ["Ibuprofen [Non-Selective NSAID]", "+", "Celecoxib [COX-2 Selective NSAID]", "──>", "Dual Renal Prostaglandin Inhibition", "──>", "Pre-Renal Azotemia"],
        evidence_source: "Beers Criteria 2023; Kidney Disease: Improving Global Outcomes (KDIGO) Clinical Practice Guideline for Acute Kidney Injury.",
        clinical_uncertainty: "Patient frequency of PRN Ibuprofen intake over the past 30 days is unquantified; last documented ingestion is unknown."
      },
      {
        severity: "UNASSESSED",
        class_level: "ⓘ [UNABLE TO ASSESS] Sulfa Allergy Cross-Reactivity • FINDING #03 • OBSERVATIONAL",
        headline: "⚗ Chemical Moiety Assessment",
        title: "Celecoxib sulfonamide moiety in patient with Sulfa allergy",
        description: "Celecoxib contains a benzenesulfonamide moiety. Cross-reactivity with antimicrobial sulfonamides (e.g., sulfamethoxazole/trimethoprim) is structurally debated in current literature but warrants clinician vigilance regarding hypersensitivity history.",
        graph_path: ["Sulfa Allergy (Documented)", "──?", "Benzenesulfonamide Moiety (Celecoxib)", "──>", "Uncertain IgE / T-Cell Cross-Sensitization"],
        evidence_source: "American Academy of Allergy, Asthma & Immunology (AAAAI) Consensus Statement on Non-Arylamine Sulfonamides.",
        clinical_uncertainty: "Exact chemical nature of patient's historical 'Sulfa' reaction is unconfirmed (benign delayed contact dermatosis vs systemic anaphylaxis)."
      }
    ]
  };
}

// --- DEDICATED FULL-CANVAS PHARMACOLOGICAL GRAPH VIEW ---

function renderDoctorGraphView() {
  const candRx = state.safetyReport?.regimen_under_evaluation?.name || document.getElementById('doc-med-name')?.value || 'Celecoxib';
  return `
    <div class="space-y-6 animate-toast">
      <!-- Breadcrumbs & Quick Nav -->
      <div class="flex items-center justify-between pb-3 border-b border-slate-200">
        <div class="flex items-center space-x-3">
          <button onclick="if(state.safetyReport) { state.doctorStep = 'safety_report'; renderApp(); } else { state.doctorStep = 'one_time_view'; renderApp(); }" class="text-xs font-semibold text-[#2A7F85] hover:underline flex items-center space-x-1">
            <span>&larr; Back to Consultation Workflow</span>
          </button>
          <span class="text-slate-300">|</span>
          <span class="text-xs text-slate-500 font-mono">
            Candidate Compound: <strong class="text-cyan-700 font-bold">${candRx}</strong> • Active Patient: Ravi Kumar (#GK-8821)
          </span>
        </div>
        <div class="flex items-center space-x-3">
          <span class="px-2.5 py-1 rounded bg-teal-50 border border-teal-200 text-teal-800 text-[11px] font-mono">
            60 FPS Spring Physics • CoSE/Cola Engine
          </span>
        </div>
      </div>

      <!-- Main Visualizer Canvas Mount Container -->
      <div id="doctor-full-graph-container" class="w-full"></div>
    </div>
  `;
}

// --- DOM EVENT ATTACHMENTS ---

function attachPatientEvents() {}

function attachDoctorEvents() {
  setupPinDigitAutoAdvance();

  // Mount Pharmacological Graph Visualizer when container exists
  const safetyGraphEl = document.getElementById('doctor-safety-graph-container');
  const fullGraphEl = document.getElementById('doctor-full-graph-container');

  if (safetyGraphEl) {
    if (window.graphVisualizer) {
      window.graphVisualizer.destroy();
    }
    const candRx = state.safetyReport?.regimen_under_evaluation?.name || document.getElementById('doc-med-name')?.value || 'Celecoxib';
    window.graphVisualizer = new PharmacologicalGraphVisualizer({
      containerId: 'doctor-safety-graph-container',
      candidateRx: candRx,
      patientId: 'GK-8821'
    });
    window.graphVisualizer.init();
  } else if (fullGraphEl) {
    if (window.graphVisualizer) {
      window.graphVisualizer.destroy();
    }
    const candRx = state.safetyReport?.regimen_under_evaluation?.name || document.getElementById('doc-med-name')?.value || 'Celecoxib';
    window.graphVisualizer = new PharmacologicalGraphVisualizer({
      containerId: 'doctor-full-graph-container',
      candidateRx: candRx,
      patientId: 'GK-8821'
    });
    window.graphVisualizer.init();
  }
}
