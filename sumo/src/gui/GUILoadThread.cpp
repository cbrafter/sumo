//---------------------------------------------------------------------------//
//                        GUILoadThread.cpp -
//  Class describing the thread that performs the loading of a simulation
//                           -------------------
//  project              : SUMO - Simulation of Urban MObility
//  begin                : Sept 2002
//  copyright            : (C) 2002 by Daniel Krajzewicz
//  organisation         : IVF/DLR http://ivf.dlr.de
//  email                : Daniel.Krajzewicz@dlr.de
//---------------------------------------------------------------------------//

//---------------------------------------------------------------------------//
//
//   This program is free software; you can redistribute it and/or modify
//   it under the terms of the GNU General Public License as published by
//   the Free Software Foundation; either version 2 of the License, or
//   (at your option) any later version.
//
//---------------------------------------------------------------------------//
namespace
{
    const char rcsid[] =
    "$Id$";
}
// $Log$
// Revision 1.21  2003/12/11 06:18:03  dkrajzew
// network loading and initialisation improved
//
// Revision 1.20  2003/12/09 11:23:07  dkrajzew
// some memory leaks removed
//
// Revision 1.19  2003/12/04 13:22:48  dkrajzew
// better error handling applied
//
// Revision 1.18  2003/11/26 09:39:13  dkrajzew
// added a logging windows to the gui (the passing of more than a single lane to come makes it necessary)
//
// Revision 1.17  2003/10/28 08:32:13  dkrajzew
// random number specification option added
//
// Revision 1.16  2003/09/22 14:54:22  dkrajzew
// some refactoring on GUILoadThread-usage
//
// Revision 1.15  2003/09/22 12:27:02  dkrajzew
// qt-includion problems patched
//
// Revision 1.14  2003/09/05 14:45:44  dkrajzew
// first tries for an implementation of aggregated views
//
// Revision 1.13  2003/07/22 14:56:46  dkrajzew
// changes due to new detector handling
//
// Revision 1.12  2003/07/07 08:09:38  dkrajzew
// Some further error checking was added and the usage of the SystemFrame was refined
//
// Revision 1.11  2003/06/24 08:09:28  dkrajzew
// implemented SystemFrame and applied the changes to all applications
//
// Revision 1.10  2003/06/19 10:56:03  dkrajzew
// user information about simulation ending added; the gui may shutdown on end and be started with a simulation now;
//
// Revision 1.9  2003/06/18 11:04:53  dkrajzew
// new error processing adapted
//
// Revision 1.8  2003/06/06 11:12:37  dkrajzew
// deletion of singletons changed/added
//
// Revision 1.7  2003/06/06 10:33:47  dkrajzew
// changes due to moving the popup-menus into a subfolder
//
// Revision 1.6  2003/06/05 06:26:16  dkrajzew
// first tries to build under linux: warnings removed; Makefiles added
//
// Revision 1.5  2003/03/20 16:17:52  dkrajzew
// windows eol removed
//
// Revision 1.4  2003/03/12 16:55:18  dkrajzew
// centering of objects debugged
//
// Revision 1.3  2003/02/07 10:34:14  dkrajzew
// files updated
//
//


/* =========================================================================
 * included modules
 * ======================================================================= */
#ifdef HAVE_CONFIG_H
#include "config.h"
#endif // HAVE_CONFIG_H

#include <sumo_version.h>
#include <iostream>
#include <guisim/GUINet.h>
#include <guinetload/GUINetBuilder.h>
#include <microsim/MSDetectorSubSys.h>
#include <utils/common/UtilExceptions.h>
#include <utils/xml/XMLBuildingExceptions.h>
#include <utils/options/OptionsCont.h>
#include <utils/options/Option.h>
#include <utils/options/OptionsIO.h>
#include <utils/options/OptionsSubSys.h>
#include <utils/common/MsgHandler.h>
#include <sumo_only/SUMOFrame.h>
#include <utils/common/MsgRetrievingFunction.h>
#include <helpers/SingletonDictionary.h>
#include "GUIApplicationWindow.h"
#include "GUILoadThread.h"
#include "QSimulationLoadedEvent.h"
#include "QMessageEvent.h"
#include "QSimulationEndedEvent.h"
#include <qthread.h>

#include <ctime>


/* =========================================================================
 * used namespaces
 * ======================================================================= */
using namespace std;


/* =========================================================================
 * member method definitions
 * ======================================================================= */
GUILoadThread::GUILoadThread(GUIApplicationWindow *mw)
    : _parent(mw)
{
    myErrorRetriever = new MsgRetrievingFunction<GUILoadThread>(this,
        &GUILoadThread::retrieveError);
    myMessageRetriever = new MsgRetrievingFunction<GUILoadThread>(this,
        &GUILoadThread::retrieveMessage);
    myWarningRetreiver = new MsgRetrievingFunction<GUILoadThread>(this,
        &GUILoadThread::retrieveWarning);
}


GUILoadThread::~GUILoadThread()
{
    delete myErrorRetriever;
    delete myMessageRetriever;
    delete myWarningRetreiver;
}


void
GUILoadThread::load(const string &file)
{
    _file = file;
    start();
}


void GUILoadThread::run()
{
    GUINet *net = 0;
    std::ostream *craw = 0;
    int simStartTime = 0;
    int simEndTime = 0;

    // remove old options
    OptionsSubSys::close();

    // register message callbacks
    MsgHandler::getErrorInstance()->addRetriever(myErrorRetriever);
    MsgHandler::getWarningInstance()->addRetriever(myWarningRetreiver);
    MsgHandler::getMessageInstance()->addRetriever(myMessageRetriever);

    // try to load the given configuration
    if(!OptionsSubSys::guiInit(SUMOFrame::fillOptions, _file)) {
        // ok, the options could not be set
        submitEndAndCleanup(net, craw, simStartTime, simEndTime);
        return;
    }
    // retrieve the options
    OptionsCont &oc = OptionsSubSys::getOptions();
    if(!SUMOFrame::checkOptions(oc)) {
        // the options are not valid
        submitEndAndCleanup(net, craw, simStartTime, simEndTime);
        return;
    }
    // try to load
    try {
        MsgHandler::getErrorInstance()->clear();
        MsgHandler::getWarningInstance()->clear();
        MsgHandler::getMessageInstance()->clear();
        GUINetBuilder builder(oc, _parent->aggregationAllowed());
        net =
            static_cast<GUINet*>(builder.buildNet());
        if(net!=0) {
            SUMOFrame::postbuild(*net);
            simStartTime = oc.getInt("b");
            simEndTime = oc.getInt("e");
            craw = SUMOFrame::buildRawOutputStream(oc);
        }
        srand(oc.getInt("srand"));
    } catch (UtilException &e) {
        string error = e.msg();
        MsgHandler::getErrorInstance()->inform(error);
        delete net;
        delete craw;
        MSNet::clearAll();
        net = 0;
        craw = 0;
    } catch (XMLBuildingException &e) {
        string error = e.getMessage("", "");
        MsgHandler::getErrorInstance()->inform(error);
        delete net;
        delete craw;
        MSNet::clearAll();
        net = 0;
        craw = 0;
    }
    submitEndAndCleanup(net, craw, simStartTime, simEndTime);
}

/*
void
GUILoadThread::inform(const std::string &msg)
{
    QThread::postEvent( _parent,
        new QMessageEvent(MsgHandler::MT_ERROR, msg));
}

*/


void
GUILoadThread::retrieveMessage(const std::string &msg)
{
    QThread::postEvent( _parent,
        new QMessageEvent(MsgHandler::MT_MESSAGE, msg));
}


void
GUILoadThread::retrieveWarning(const std::string &msg)
{
    QThread::postEvent( _parent,
        new QMessageEvent(MsgHandler::MT_WARNING, msg));
}


void
GUILoadThread::retrieveError(const std::string &msg)
{
    QThread::postEvent( _parent,
        new QMessageEvent(MsgHandler::MT_ERROR, msg));
}




void
GUILoadThread::submitEndAndCleanup(GUINet *net, std::ostream *craw,
                                   int simStartTime, int simEndTime)
{
    // remove message callbacks
    MsgHandler::getErrorInstance()->removeRetriever(myErrorRetriever);
    MsgHandler::getWarningInstance()->removeRetriever(myWarningRetreiver);
    MsgHandler::getMessageInstance()->removeRetriever(myMessageRetriever);
    // inform parent about the process
    QThread::postEvent( _parent,
        new QSimulationLoadedEvent(net, craw, simStartTime, simEndTime,
        string(_file)) );
}



/**************** DO NOT DEFINE ANYTHING AFTER THE INCLUDE *****************/
//#ifdef DISABLE_INLINE
//#include "GUILoadThread.icc"
//#endif

// Local Variables:
// mode:C++
// End:


