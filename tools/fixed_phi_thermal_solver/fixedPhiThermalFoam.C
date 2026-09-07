#include "argList.H"
#include "Time.H"
#include "fvMesh.H"
#include "fluid.H"
#include "solid.H"

using namespace Foam;

int main(int argc, char *argv[])
{
    argList::addNote("Fixed-flow thermal-only two-region CHT driver");
    #include "setRootCase.H"
    #include "createTime.H"

    word fluidName("fluid");
    word solidName("aluminum");

    fvMesh fluidMesh(IOobject(fluidName, runTime.name(), runTime, IOobject::MUST_READ));
    fvMesh solidMesh(IOobject(solidName, runTime.name(), runTime, IOobject::MUST_READ));

    solvers::fluid fluidSolver(fluidMesh);
    solvers::solid solidSolver(solidMesh);

    dictionary controls(runTime.controlDict().optionalSubDict("fixedPhiThermal"));
    const label nOuter = controls.lookupOrDefault<label>("nOuterCorrectors", 2);

    while (runTime.loop())
    {
        Info<< "Time = " << runTime.userTimeName() << nl << endl;

        fluidSolver.fvModels().correct();
        solidSolver.fvModels().correct();
        fluidSolver.prePredictor();
        solidSolver.prePredictor();
        fluidSolver.thermophysicalTransportPredictor();
        solidSolver.thermophysicalTransportPredictor();

        for (label i = 0; i < nOuter; ++i)
        {
            fluidSolver.thermophysicalPredictor();
            solidSolver.thermophysicalPredictor();
        }

        fluidSolver.thermophysicalTransportCorrector();
        solidSolver.thermophysicalTransportCorrector();
        fluidSolver.postSolve();
        solidSolver.postSolve();

        runTime.write();
        Info<< "ExecutionTime = " << runTime.elapsedCpuTime() << " s"
            << "  ClockTime = " << runTime.elapsedClockTime() << " s"
            << nl << endl;
    }

    Info<< "End" << nl << endl;
    return 0;
}
