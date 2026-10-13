// Service evidence is a dated report, not a promise of current availability.
// Non-cancelled reports may contain forecasts; transfer feasibility below uses
// only actual (REAL) event times normalized to UTC by the adapter.
Trip { tripRef: String, observedOn: String, serviceCategory: String,
       lineText: String, operator: String }.
StopEvent { ofTrip: Trip, atStopPoint: StopPoint, stopSequence: Integer,
            scheduledArrival: String, scheduledDeparture: String,
            arrivalEpoch: Integer, departureEpoch: Integer,
            cancelled: Boolean, passThrough: Boolean,
            nextRealStop: StopEvent, nextRideStop: StopEvent, rideSegment: RideSegment }.
RideSegment { ofTrip: Trip }.
declare required RideSegment ofTrip.
declare required Trip tripRef.
declare required StopEvent ofTrip.
declare required StopEvent atStopPoint.
declare functional ofTrip.
declare functional stopSequence.

?E:ReportedStop <- ?E:StopEvent AND ?E[cancelled -> false] AND ?E[passThrough -> false].
?S[servedByTrip -> ?T] <- ?E:ReportedStop AND ?E[atStopPoint -> ?S] AND ?E[ofTrip -> ?T].
?S[servedByCategory -> ?C] <- ?S[servedByTrip -> ?T] AND ?T[serviceCategory -> ?C].
?S:LongDistanceStation <- ?S[servedByCategory -> "IC"].
?S:LongDistanceStation <- ?S[servedByCategory -> "EC"].
?S:LongDistanceStation <- ?S[servedByCategory -> "ICE"].
?S:LongDistanceStation <- ?S[servedByCategory -> "IR"].
?S:LongDistanceStation <- ?S[servedByCategory -> "TGV"].
?S:LongDistanceStation <- ?S[servedByCategory -> "RJ"].
?S:LongDistanceStation <- ?S[servedByCategory -> "RJX"].
?S:LongDistanceStation <- ?S[servedByCategory -> "NJ"].

// Both endpoints must be real reports on the SAME dated trip. nextRealStop
// never bridges an omitted station; nextRideStop may span known foreign stops.
?T[offersNonStop(?A) -> ?B] <-
    ?E:ReportedStop AND ?E[ofTrip -> ?T] AND ?E[atStopPoint -> ?A]
    AND ?E[nextRealStop -> ?F] AND ?F:ReportedStop AND ?F[ofTrip -> ?T]
    AND ?F[atStopPoint -> ?B] AND ?A != ?B
    AND ?E[stopSequence -> ?I] AND ?F[stopSequence -> ?J] AND ?I < ?J.
?A[nonStopTo -> ?B] <- ?T[offersNonStop(?A) -> ?B].

// Journey results belong to a request, never to the whole station graph.
// A request supplies a day, earliest actual departure and explicit transfer
// allowance. The adapter supplies transferReady(request) = arrival + allowance;
// the engine checks trip identity, times and each step of the journey.
// Keep station/segment lookups before ReportedStop membership when binding a
// new event: the reverse order multiplies candidates across the whole snapshot.
JourneyRequest { origin: StopPoint, onDay: String, notBefore: Integer,
                 transferSeconds: Integer }.
declare required JourneyRequest origin.
declare required JourneyRequest onDay.
declare required JourneyRequest notBefore.
declare required JourneyRequest transferSeconds.
?R[board0 -> ?E] <- ?R:JourneyRequest AND ?R[origin -> ?S]
    AND ?R[onDay -> ?D] AND ?R[notBefore -> ?Min]
    AND ?E[atStopPoint -> ?S] AND ?E:ReportedStop AND ?E[ofTrip -> ?T]
    AND ?T[observedOn -> ?D] AND ?E[departureEpoch -> ?Dep] AND ?Dep >= ?Min.

// Compare ordered events within one uninterrupted trip segment. This avoids
// recursive materialization along every intermediate stop of the same train.
?R[reached0 -> ?F] <- ?R[board0 -> ?E] AND ?E[ofTrip -> ?T]
    AND ?E[rideSegment -> ?Segment] AND ?E[stopSequence -> ?I]
    AND ?F[rideSegment -> ?Segment] AND ?F:ReportedStop AND ?F[ofTrip -> ?T]
    AND ?F[stopSequence -> ?J] AND ?I < ?J.

?R[board1 -> ?F] <- ?R[reached0 -> ?E] AND ?E[atStopPoint -> ?S]
    AND ?E[ofTrip -> ?T1] AND ?E[transferReady(?R) -> ?Ready]
    AND ?F[atStopPoint -> ?S] AND ?F:ReportedStop AND ?F[ofTrip -> ?T2]
    AND ?T1 != ?T2 AND ?R[onDay -> ?Day] AND ?T2[observedOn -> ?Day]
    AND ?F[departureEpoch -> ?Dep] AND ?Dep >= ?Ready.
// Compare ordered events within one uninterrupted trip segment. This avoids
// recursive materialization along every intermediate stop of the same train.
?R[reached1 -> ?F] <- ?R[board1 -> ?E] AND ?E[ofTrip -> ?T]
    AND ?E[rideSegment -> ?Segment] AND ?E[stopSequence -> ?I]
    AND ?F[rideSegment -> ?Segment] AND ?F:ReportedStop AND ?F[ofTrip -> ?T]
    AND ?F[stopSequence -> ?J] AND ?I < ?J.

?R[board2 -> ?F] <- ?R[reached1 -> ?E] AND ?E[atStopPoint -> ?S]
    AND ?E[ofTrip -> ?T1] AND ?E[transferReady(?R) -> ?Ready]
    AND ?F[atStopPoint -> ?S] AND ?F:ReportedStop AND ?F[ofTrip -> ?T2]
    AND ?T1 != ?T2 AND ?R[onDay -> ?Day] AND ?T2[observedOn -> ?Day]
    AND ?F[departureEpoch -> ?Dep] AND ?Dep >= ?Ready.
// Compare ordered events within one uninterrupted trip segment. This avoids
// recursive materialization along every intermediate stop of the same train.
?R[reached2 -> ?F] <- ?R[board2 -> ?E] AND ?E[ofTrip -> ?T]
    AND ?E[rideSegment -> ?Segment] AND ?E[stopSequence -> ?I]
    AND ?F[rideSegment -> ?Segment] AND ?F:ReportedStop AND ?F[ofTrip -> ?T]
    AND ?F[stopSequence -> ?J] AND ?I < ?J.

?R[reachDirect -> ?S] <- ?R[reached0 -> ?E] AND ?E[atStopPoint -> ?S]
    AND ?R[origin -> ?A] AND ?S != ?A.
?R[reach1 -> ?S] <- ?R[reachDirect -> ?S].
?R[reach1 -> ?S] <- ?R[reached1 -> ?E] AND ?E[atStopPoint -> ?S]
    AND ?R[origin -> ?A] AND ?S != ?A.
?R[reach2 -> ?S] <- ?R[reach1 -> ?S].
?R[reach2 -> ?S] <- ?R[reached2 -> ?E] AND ?E[atStopPoint -> ?S]
    AND ?R[origin -> ?A] AND ?S != ?A.

// The witness retains the actual interchange and its second-train segment.
?R[changeAt(?S) -> ?B] <- ?R[board1 -> ?E] AND ?E[atStopPoint -> ?S]
    AND ?E[ofTrip -> ?T] AND ?E[rideSegment -> ?Segment] AND ?E[stopSequence -> ?I]
    AND ?F[rideSegment -> ?Segment] AND ?F:ReportedStop AND ?F[ofTrip -> ?T]
    AND ?F[stopSequence -> ?J] AND ?I < ?J AND ?F[atStopPoint -> ?B]
    AND ?R[origin -> ?A] AND ?A != ?B.
