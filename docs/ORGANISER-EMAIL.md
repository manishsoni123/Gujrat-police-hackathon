# E-mail to the organisers — ready to send

Send from the registered team e-mail. Send tonight (4 September); phone follow-up on +91 95370 89982 the next morning if there is no reply, because Q4 and Q5 change our build and sizing.

---

**To:** sentinel.hackathon@gujarat.gov.in
**Subject:** Sentinel Hackathon 2026 – Category 2 (Company/Systems Integrator) – questions from Dynatech Consultancy before the 7 September submission

Dear Sentinel Hackathon Team,

Dynatech Consultancy has registered for the Gujarat Police Innovation Challenge 2026 / CCTV Integration Hackathon under Category 2 (Company / Systems Integrator), team lead `[FILL: name]`, registered e-mail `[FILL: e-mail]`, mobile `[FILL: number]`. We are building a Model 1 + Model 2 submission, **Sentinel Gujarat** (centralised registry with GIS, unified viewing, ANPR, watchlist alerts and vehicle movement history), against the sandbox feeds and would be grateful for answers to the following points, several of which affect how we size and host our solution before the deadline.

**Registration and team**

1. Is there a minimum or maximum team size, and must every team member register individually on the portal, or is one registration per team sufficient?
2. May a team member who joins after our registration be added later, and how?

**Intellectual property and licensing**

3. Who owns the intellectual property in the submitted code, documents and videos, and under what terms may Gujarat Police use them after the hackathon? We intend to release our code under an open-source licence (MIT) in line with the portal's open-source requirement; please confirm this is acceptable.

**Sandbox access (affects our hosting decision tonight)**

4. Is the sandbox host (RTSP :8554, WHEP :8889, HLS :80, catalogue `/api/ingest`) reachable from a public cloud IP address (AWS/Azure India regions), or only from specific whitelisted IPs / a VPN? If whitelisting is required, please let us know the procedure and to which address we should send our IP.
5. Are the sandbox feeds expected to stay available continuously until the submission deadline and, for shortlisted teams, through 11 September?

**Phase 2 (Grand Finale) environment (affects live-ANPR sizing)**

6. Will the Phase 2 live test use the same sandbox host and the same ~50 looping camera feeds, or a different environment (new host, different cameras, fresh non-looping footage)? Will a camera catalogue endpoint of the same `/api/ingest` shape be available on-site, or should we expect a camera list in another format?
7. What network will be available at i-Hub Gujarat for the finalists (wired LAN to the camera environment, Wi-Fi, ability to use our own 4G/5G hotspot, outbound internet to reach our cloud VM, UDP allowed for WebRTC)?
8. Will the jury open our hosted platform on their own devices, or view it only on our screen?

**Submission**

9. Is the 7 September deadline end-of-day IST (23:59) or an earlier hour? We are planning to submit by 12:00 IST to be safe.
10. Is there a length limit for Video 2 (government feed + output report)? Video 1 is understood to be 2–3 minutes maximum.
11. Are scoring weights for the seven evaluation areas available, or are all areas weighted equally?
12. Are there file-size or format restrictions for the uploaded documents (PPT/PDF), and should the output report be uploaded to the portal as well as linked from the Drive folder?

**Logistics for finalists**

13. If shortlisted, is travel and accommodation for the team provided or reimbursed for 10–11 September, and for how many members?
14. Could you share the hour-by-hour agenda for 10–11 September (setup time, presentation slot length, live-test duration, Q&A) and the table/power/display facilities available to each team?

Thank you for organising the challenge and for the sandbox resources and sample code, which we have found very helpful.

Kind regards,

`[FILL: Name]`
`[FILL: Designation]`, Dynatech Consultancy
`[FILL: mobile]` · `[FILL: e-mail]`
Team registration ID: `[FILL]`

---

## 2. Follow-up phone script (if no reply by 09:30 IST on 5 September)

"Good morning, this is `[name]` from Dynatech Consultancy, registered in Category 2 of the Sentinel hackathon. We e-mailed last night with a few questions; two of them decide our hosting today: is the sandbox reachable from a public cloud IP or only from whitelisted addresses, and will the Phase 2 test use the same sandbox feeds or a new environment? Could you help with those two now, and reply to the rest by e-mail?"

## 3. Template — submission confirmation (send only if the portal shows no confirmation page)

**Subject:** Sentinel Hackathon 2026 – Category 2 – submission confirmation request – Dynatech Consultancy

Dear Sentinel Hackathon Team, we submitted our Phase 1 package through the portal on 7 September 2026 at `[time IST]` (hosted URL `[URL]`, GitHub `[URL]`, Drive `[URL]`, two unlisted YouTube links). The portal did not display a confirmation page; could you please confirm that the submission has been received? Kind regards, `[name]`, Dynatech Consultancy, `[mobile]`.
