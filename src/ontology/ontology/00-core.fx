// ====================================================================
// Swiss rail passenger transport -- core meta model
//
// Identity, provenance and scope. This is the only layer that declares
// the world assumption; every other layer and every generated data file
// is loaded alongside it.
// ====================================================================

// Open world: a stop point we hold no evidence about is `unknown`, not
// `false`. Individual slots are closed later, and only where the source
// dataset really is the complete inventory for that slot.
world open.

// --------------------------------------------------------------------
// Stop points
//
// Identity is the national BPUIC number, never the name. Two stations
// can carry the same display name, so `designation` is a plain slot and
// `sp_8509000` is the object.
// --------------------------------------------------------------------
StopPoint {
    bpuic: Integer,
    designation: String,
    abbreviation: String,
    country: String,
    isStopPoint: Boolean,
    hasTimetable: Boolean,
    isFreightPoint: Boolean,
    validFrom: String,
    validTo: String,
    height: Float
}.

declare required StopPoint bpuic.
declare functional bpuic.
declare functional designation.

// --------------------------------------------------------------------
// Scope, derived rather than filtered
//
// "Swiss, passenger, rail" is a modelling decision, so it is stated as
// rules the engine can explain -- not as a Python filter whose reasoning
// disappears. `framex explain 'sp_8509000:TrainStopPoint'` shows why a
// station is in scope.
// --------------------------------------------------------------------
SwissStopPoint extends StopPoint.
PassengerStopPoint extends SwissStopPoint.
TrainStopPoint extends PassengerStopPoint.

?S:SwissStopPoint <- ?S:StopPoint AND ?S[country -> "CH"].
?S:PassengerStopPoint <- ?S:SwissStopPoint AND ?S[isStopPoint -> true].
?S:TrainStopPoint <- ?S:PassengerStopPoint AND ?S[servesMode -> mode_train].

// --------------------------------------------------------------------
// Datasets as first-class objects
//
// Every layer contributes one Dataset object recording what was loaded
// and when, so `?D:Dataset` reports the state of the knowledge base.
//
// `provenanceMode` says how finely that source is attached:
//
//   "fact"  -- every statement carries an `annotate` clause, so
//              `framex explain` names the source of each one.
//   "layer" -- the source is recorded here only. Per-fact annotation
//              was measured to become superlinear past roughly 70,000
//              annotations, so the high-volume layers (observed service,
//              passenger frequency, sector boards) stay queryable by
//              recording provenance once instead of 70,000 times.
//
// `completeFor` names the slots a dataset is the full inventory for --
// the modelling contract that licenses a `closed slot` declaration. It
// appears only when the ingest could place every row; a partially
// resolved inventory must not turn absence into `false`.
// --------------------------------------------------------------------
Dataset {
    datasetId: String,
    title: String,
    retrievedAt: String,
    rowCount: Integer,
    statementCount: Integer,
    provenanceMode: String,
    completeFor: String
}.

declare required Dataset datasetId.

// A slot whose inventory some loaded dataset vouches for.
?S:ClosedInventory <- ?D:Dataset AND ?D[completeFor -> ?S].
