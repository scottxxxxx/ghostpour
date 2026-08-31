# The silent-meeting cohort, ready to join

**Purpose:** settle whether Scott's tire-store case generalises, i.e. whether
meetings that yield only behaviour patches are *short* or are *project-less*.
Those point at completely different fixes and a sample of two cannot separate
them. See `2026-08-30-recall-scoping-posture.md` for why it matters: option C
is largely justified by serving project-less meetings well, and we do not yet
know whether those meetings have anything to serve.

**Status: NOT RUN.** Prod container exec was blocked for the session that
wrote this. The cohort is CQ's (measured), the join is GP's (unrun). This file
exists so the measurement survives the chat it was assembled in.

---

## The query

Join on `meeting_id`. GP holds the transcript, CQ holds the yield.

    meeting_transcripts.meeting_id  ->  the 34 / the control below
    length(meeting_transcripts.transcript)
    meeting_transcripts.project_id  (also: is it even populated? see below)

`meeting_transcripts` retains 30 days (`TRANSCRIPT_RETENTION_DAYS = 30`,
`app/services/retention.py:29`) and is written on every
`/v1/capture-transcript` (`app/routers/cq_proxy.py:306`). Every id below is
inside that window as of 2026-08-30.

**The control group is the point.** Lengths against the silent 34 alone prove
nothing: any set of meetings has a length distribution. The question is
whether the silent set is SHORTER than the productive set. If the two overlap,
the content hypothesis fails and the project-less hypothesis survives.

---

## Silent set: 34 meetings, no non-behavior patch EVER

All `origin_type=meeting`. Column is behaviour-patch count.

    2ABDD4ED-9DDD-43F2-813B-EB80BC515699   1    <- the tire store, 08-29 15:47
    EA3C5976-1474-4AAA-9DEB-832B8EA8C6C6  17    <- breaks "too short" on its own
    4FDDB6B0-D605-45B1-8ADC-3BA2F4A80023  15
    6F7FFB63-89FD-488E-97EC-3CBD1F7A5688  15
    E85525F6-735E-4C68-A4E9-AC0E86179951  15
    2E206B94-61DC-42AC-88E5-15FC0074197A  12
    796D53C3-6036-4F7D-A642-0D652DD16F2A  12
    91B26A44-9183-4B72-A0E3-BE2A1FF92AEF  12
    AFADE3CC-8BB3-4D01-9EFF-2E369B0EDFB6  12
    26441242-AE5C-4C3A-A8B8-EE171BBF1771  12
    58E50667-5D5F-4B63-9204-314B5E6330CC  12
    A7E0B52C-75E7-4EB1-85C8-4B871CC1764A  10
    BDE2D119-7F53-4FB6-93F6-E842838101C9  10
    4B92676C-0A58-4583-867D-9E50C1C62609   9
    E120ECE1-919E-4D0D-BA27-EC0287D6016C   8
    4E9BD912-A859-4270-94DF-D3A27F6D054C   7
    6212B679-AAE7-4A1F-A012-B838CE0B1C47   6
    0D4DE3C2-B15C-4DB5-857B-0D7330450558   5
    F95AF6D8-099A-47E3-87E0-D02DD7C1BDD6   5
    894BA5D6-2492-4780-8781-CC7DCD70A13C   5
    88F0083B-0BBF-4D8C-84E4-2BF0E349F7AF   5
    B25789FE-9156-4311-AEB0-4600F671DDC0   4
    FAE5ED7E-4D32-4A3B-B667-0EE9B13AD5ED   4
    EB902A71-F3D3-4EF0-ADE4-EBCAB0B41144   4
    D24829FE-D323-4346-8D99-040A6F45D465   3
    AD33DD2B-3197-470C-91C2-B155C58A2506   3
    24DFC8E1-720F-4781-B057-4CC6C4097D48   3
    FCB15536-7556-4E6F-B56D-911599E36F0C   3
    FB7E7B82-7CA3-47CB-B9FD-74FECA511CCC   3
    A1BEA9DD-F745-4384-8BDF-70C0C90D0E40   2
    9D5E530B-34CB-47E8-9C7C-EDA712A5D8E5   2
    09405A83-B2FF-42A6-8EEA-CA4AE3DFCCAE   2
    3712B868-4DA0-4321-8010-7B8C64EADBDF   2
    8753CA3D-BFEF-4A4E-BE98-0F78E64CB3F2   1

## Control: the 10 most productive meetings in the same window

Column is total patch count.

    68EB5A76-31C6-4171-898D-00220DDA81F7  43
    D6A5B88D-1BFC-415C-B65C-92DDA4E9C5D1  42
    42206469-E95A-4F72-8C68-5D9DEDA14B84  41
    770C61D3-CF8D-4EFD-BE83-8AE0DC80A3FD  40
    BE739982-DBEA-4FB2-BC40-CAEC2ED2B947  40
    EED21245-AF8F-4F53-98B1-2478702BC527  37
    4EC4FEA7-ADB0-4D93-B7AA-BDBB12D68E95  32
    26E316E1-8E91-4555-9DF5-71C28B7F4D5D  31
    738076D0-F3C5-4C8C-A7B6-8F292107AAEF  31
    A0E443B9-EE56-4527-B66C-C437A46F1725  30

## ⚠ EXCLUDED. Do not put these in the join.

These four looked silent under a 14-day window and are not. They were part of
a backfill that re-ran the behaviour call over historical meetings on 08-17,
so their non-behavior patches predate the window rather than not existing.
One has 28 non-behavior patches from 2026-06-25. **A 14-day join on GP's side
would reproduce exactly this artifact**, which is why they are named rather
than quietly dropped.

    EBD40AB3
    22FB3590
    9E153EB5
    731BDB70

---

## Two traps already caught in producing this, both by CQ

Recorded because both fired CLEANLY and answered a slightly different question
than the one asked, which is the failure mode that survives review.

1. **The circular project signal.** A first cut asked whether any patch on the
   meeting carried a project and printed a perfect correlation: 100% of
   project-less meetings silent, 0% of project-having meetings silent. It was
   spurious. Only 79 of 1486 behavior patches are scoped, so "every patch is
   behavior" nearly *entails* "no patch is scoped". Two variables that were
   one variable, and a beautifully quotable false result.
2. **The windowed backfill**, above: 38 became 34.

## One thing to check BEFORE building on the join

Whether `meeting_transcripts.project_id` is reliably populated is
**unmeasured**. The column exists and the write passes the client's value
through unconditionally, which is not the same as it being non-null in
practice. CQ notes from their side that the ingest-request project is what
stamps CQ patches, **so a null there would be systematic rather than random**.
Check the null rate first; if it is high, the join answers less than it
appears to.
